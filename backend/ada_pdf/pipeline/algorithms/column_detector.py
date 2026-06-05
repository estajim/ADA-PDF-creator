"""Detect multi-column layout and assign blocks to column zones."""
from dataclasses import dataclass, field

from ada_pdf.models.domain import BlockIR, BBox


@dataclass
class ColumnZone:
    index: int          # 0 = leftmost column
    x_min: float
    x_max: float
    blocks: list[BlockIR] = field(default_factory=list)

    def contains_x(self, x: float) -> bool:
        return self.x_min <= x <= self.x_max


_GAP_THRESHOLD = 0.12   # minimum gap as fraction of page width to separate columns
_FULL_WIDTH_SPAN = 0.80  # block spanning > 80% of page width is full-width


def detect(
    blocks: list[BlockIR],
    page_width: float,
    algorithm: str = "gap",
) -> list[ColumnZone]:
    """Return ordered list of ColumnZones detected on the page.

    A single-column page returns one ColumnZone spanning the full width.
    """
    content_blocks = [b for b in blocks if not b.is_artifact]
    if not content_blocks:
        return [ColumnZone(index=0, x_min=0, x_max=page_width)]

    if algorithm == "kmeans":
        return _detect_kmeans(content_blocks, page_width)
    return _detect_gap(content_blocks, page_width)


def _detect_gap(blocks: list[BlockIR], page_width: float) -> list[ColumnZone]:
    """Gap analysis on x-center distribution."""
    x_centers = sorted(b.bbox.x_center for b in blocks)
    if len(x_centers) <= 1:
        return [ColumnZone(index=0, x_min=0, x_max=page_width)]

    # Find gaps larger than the threshold
    gap_threshold = page_width * _GAP_THRESHOLD
    boundaries: list[float] = [0.0]
    for i in range(1, len(x_centers)):
        if x_centers[i] - x_centers[i - 1] > gap_threshold:
            midpoint = (x_centers[i] + x_centers[i - 1]) / 2
            boundaries.append(midpoint)
    boundaries.append(page_width)

    zones = [
        ColumnZone(index=i, x_min=boundaries[i], x_max=boundaries[i + 1])
        for i in range(len(boundaries) - 1)
    ]

    # Assign blocks to zones
    for block in blocks:
        xc = block.bbox.x_center
        for zone in zones:
            if zone.contains_x(xc):
                zone.blocks.append(block)
                break
        else:
            zones[-1].blocks.append(block)  # fallback to last zone

    # Sort blocks within each zone by Y
    for zone in zones:
        zone.blocks.sort(key=lambda b: b.bbox.y0)

    return zones


def _detect_kmeans(blocks: list[BlockIR], page_width: float) -> list[ColumnZone]:
    """Simple 1-D k-means for up to 4 columns. Falls back to gap on failure."""
    try:
        import numpy as np  # optional dep for kmeans path

        x_centers = np.array([b.bbox.x_center for b in blocks]).reshape(-1, 1)

        best_k = 1
        best_inertia = float("inf")
        best_labels = np.zeros(len(blocks), dtype=int)
        best_centers = np.array([page_width / 2])

        for k in range(1, min(5, len(blocks) + 1)):
            from sklearn.cluster import KMeans  # type: ignore[import]
            km = KMeans(n_clusters=k, random_state=0, n_init=5)
            km.fit(x_centers)
            # Only accept k if the minimum inter-cluster gap > threshold
            centers = sorted(km.cluster_centers_.flatten())
            gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
            if gaps and min(gaps) < page_width * _GAP_THRESHOLD:
                continue
            if km.inertia_ < best_inertia:
                best_inertia = km.inertia_
                best_k = k
                best_labels = km.labels_
                best_centers = km.cluster_centers_.flatten()

        # Build zones from cluster centers
        sorted_centers = sorted(enumerate(best_centers), key=lambda x: x[1])
        boundaries = [0.0]
        for i in range(len(sorted_centers) - 1):
            mid = (sorted_centers[i][1] + sorted_centers[i + 1][1]) / 2
            boundaries.append(mid)
        boundaries.append(page_width)

        zones = [
            ColumnZone(index=i, x_min=boundaries[i], x_max=boundaries[i + 1])
            for i in range(best_k)
        ]
        for block, label in zip(blocks, best_labels):
            # Map label to sorted zone index
            orig_idx = next(i for i, (orig, _) in enumerate(sorted_centers) if orig == label)
            zones[orig_idx].blocks.append(block)
        for zone in zones:
            zone.blocks.sort(key=lambda b: b.bbox.y0)
        return zones

    except ImportError:
        return _detect_gap(blocks, page_width)


def is_full_width(block: BlockIR, page_width: float) -> bool:
    return block.bbox.width >= page_width * _FULL_WIDTH_SPAN
