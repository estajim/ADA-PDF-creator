/* ── ADA PDF Converter — UI logic ─────────────────────────────────────── */

const API_KEY = 'test-key-local';

// ── DOM refs ────────────────────────────────────────────────────────────
const uploadPanel     = document.getElementById('upload-panel');
const progressPanel   = document.getElementById('progress-panel');
const resultPanelSingle = document.getElementById('result-panel-single');
const resultPanelMulti  = document.getElementById('result-panel-multi');

const dropZone        = document.getElementById('drop-zone');
const fileInput       = document.getElementById('file-input');
const browseBtn       = document.getElementById('browse-btn');
const fileQueueEl     = document.getElementById('file-queue');
const convertBtn      = document.getElementById('convert-btn');
const convertBtnLabel = document.getElementById('convert-btn-label');

// Progress
const progressTitle   = document.getElementById('progress-title');
const progressFilename = document.getElementById('progress-file-name');
const queueTracker    = document.getElementById('queue-tracker');
const progressBar     = document.getElementById('progress-bar');
const progressPct     = document.getElementById('progress-pct');
const progressBarWrap = progressBar.closest('[role="progressbar"]');
const cancelBtn       = document.getElementById('cancel-btn');

// Single-file result
const downloadBtn     = document.getElementById('download-btn');
const pdfPreview      = document.getElementById('pdf-preview');
const reportDetails   = document.getElementById('report-details');
const reportBadge     = document.getElementById('report-badge');
const reportStats     = document.getElementById('report-stats');
const reportIssues    = document.getElementById('report-issues');
const pacScoreRow     = document.getElementById('pac-score-row');
const pacScoreBadge   = document.getElementById('pac-score-badge');
const pacScoreText    = document.getElementById('pac-score-text');
const pacIssuesWrap   = document.getElementById('pac-issues-wrap');
const pacIssuesEl     = document.getElementById('pac-issues');
const resultBadge     = document.getElementById('result-badge');
const badgeIcon       = document.getElementById('badge-icon');
const badgeLabel      = document.getElementById('badge-label');
const badgeScore      = document.getElementById('badge-score');
const resultPages     = document.getElementById('result-pages');
const resultTime      = document.getElementById('result-time');
const errorCard       = document.getElementById('error-card');
const errorMessage    = document.getElementById('error-message');
const newConvBtn      = document.getElementById('new-conversion-btn');

// Multi-file result
const batchSummary    = document.getElementById('batch-summary');
const resultCards     = document.getElementById('result-cards');
const downloadAllBtn  = document.getElementById('download-all-btn');
const downloadAllCount = document.getElementById('download-all-count');
const newBatchBtn     = document.getElementById('new-batch-btn');

// ── State ────────────────────────────────────────────────────────────────
// Each entry: { file: File, id: string (unique UI id) }
let selectedFiles = [];
let cancelled     = false;
let pollTimer     = null;

// Results keyed by UI id: { jobId, status, statusData, filename }
let fileResults   = {};

const STAGE_KEYS = [
  'ingesting','extracting','ocr','layout_analysis',
  'structure_detection','generating_alt_text',
  'generating_pdf','writing_metadata','completed',
];

// ── File selection ───────────────────────────────────────────────────────

// Guard against double-open: the file input used to be a full-size overlay,
// which caused the dialog to re-open on close via a browser synthetic click.
// Now the input is off-screen; all opens go through openFileDialog().
let _dialogGuard = false;

function openFileDialog() {
  if (_dialogGuard) return;
  _dialogGuard = true;
  fileInput.click();
  // Reset after 2s so a cancelled dialog (no change event) doesn't lock things.
  setTimeout(() => { _dialogGuard = false; }, 2000);
}

browseBtn.addEventListener('click', (e) => { e.stopPropagation(); openFileDialog(); });
dropZone.addEventListener('click', openFileDialog);
dropZone.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openFileDialog(); }
});

fileInput.addEventListener('change', () => {
  _dialogGuard = false;   // release guard as soon as change fires
  if (fileInput.files.length) addFiles(Array.from(fileInput.files));
  fileInput.value = '';   // allow re-selecting the same files later
});

dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('drag-over'); });
['dragleave', 'dragend'].forEach(evt =>
  dropZone.addEventListener(evt, () => dropZone.classList.remove('drag-over'))
);
dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  const pdfs = Array.from(e.dataTransfer.files).filter(f => f.type === 'application/pdf' || f.name.endsWith('.pdf'));
  if (!pdfs.length) { showDropError('Please drop PDF files only.'); return; }
  addFiles(pdfs);
});

function addFiles(newFiles) {
  const existing = new Set(selectedFiles.map(f => f.file.name + f.file.size));
  for (const file of newFiles) {
    if (!file.name.endsWith('.pdf') && file.type !== 'application/pdf') continue;
    if (existing.has(file.name + file.size)) continue;  // skip duplicates
    const id = 'f-' + Math.random().toString(36).slice(2, 9);
    selectedFiles.push({ file, id });
    existing.add(file.name + file.size);
  }
  renderFileQueue();
}

function removeFile(id) {
  selectedFiles = selectedFiles.filter(f => f.id !== id);
  renderFileQueue();
}

function renderFileQueue() {
  const count = selectedFiles.length;
  if (count === 0) {
    fileQueueEl.classList.add('hidden');
    dropZone.classList.remove('has-file');
    document.getElementById('drop-title').textContent = 'Drop your PDFs here';
    convertBtn.disabled = true;
    convertBtn.setAttribute('aria-disabled', 'true');
    convertBtnLabel.textContent = 'Convert to Accessible PDF';
    return;
  }

  fileQueueEl.classList.remove('hidden');
  dropZone.classList.add('has-file');
  document.getElementById('drop-title').textContent =
    count === 1 ? '1 PDF selected' : `${count} PDFs selected`;

  fileQueueEl.innerHTML = selectedFiles.map(({ file, id }) => `
    <li class="file-queue-item" role="listitem">
      <svg class="fq-icon" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
        <path fill-rule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clip-rule="evenodd"/>
      </svg>
      <span class="fq-name" title="${escHtml(file.name)}">${escHtml(file.name)}</span>
      <span class="fq-size">${formatBytes(file.size)}</span>
      <button class="fq-remove" data-id="${id}" aria-label="Remove ${escHtml(file.name)}">✕</button>
    </li>
  `).join('');

  fileQueueEl.querySelectorAll('.fq-remove').forEach(btn => {
    btn.addEventListener('click', () => removeFile(btn.dataset.id));
  });

  convertBtn.disabled = false;
  convertBtn.removeAttribute('aria-disabled');
  convertBtnLabel.textContent = count === 1
    ? 'Convert to Accessible PDF'
    : `Convert ${count} PDFs`;
}

function showDropError(msg) {
  const hint = document.getElementById('drop-hint');
  hint.innerHTML = `<span style="color:var(--danger)">${msg}</span>`;
  setTimeout(() => {
    hint.innerHTML = `or <button class="browse-btn" id="browse-btn">browse files</button>`;
    document.getElementById('browse-btn').addEventListener('click', (e) => {
      e.stopPropagation(); fileInput.click();
    });
  }, 3000);
}

// ── Chip styling ──────────────────────────────────────────────────────────
document.querySelectorAll('.chip input[type="radio"]').forEach(radio => {
  radio.addEventListener('change', () => {
    radio.closest('.option-chips').querySelectorAll('.chip')
         .forEach(c => c.classList.remove('chip-active'));
    radio.closest('.chip').classList.add('chip-active');
    updateDescriptions();
  });
});

function updateDescriptions() {
  const dt = document.querySelector('input[name="doc_type"]:checked')?.value;
  const cm = document.querySelector('input[name="conversion_mode"]:checked')?.value;
  const map = {
    doc_type: {
      auto: 'Auto-detect inspects each page. Choose <em>Scanned</em> if the PDF is a photograph.',
      digital: 'Native text extraction — fast and lossless. No OCR needed.',
      scanned: 'Full OCR applied to every page using Surya (90+ languages).',
    },
    conversion_mode: {
      rebuild: '<strong>Linearize</strong> rebuilds the PDF from scratch for maximum accessibility.',
      tag_in_place: '<strong>Keep formatting</strong> preserves the original layout, adding tags in place.',
    },
  };
  if (dt) document.getElementById('doc-type-desc').innerHTML = map.doc_type[dt];
  if (cm) document.getElementById('conv-mode-desc').innerHTML = map.conversion_mode[cm];
}

// ── Convert ───────────────────────────────────────────────────────────────
convertBtn.addEventListener('click', startBatch);

async function startBatch() {
  if (!selectedFiles.length) return;
  cancelled = false;
  fileResults = {};

  const isSingle = selectedFiles.length === 1;

  if (isSingle) {
    progressTitle.textContent = 'Making your PDF accessible…';
    queueTracker.classList.add('hidden');
  } else {
    progressTitle.textContent = `Converting ${selectedFiles.length} PDFs…`;
    renderQueueTracker();
    queueTracker.classList.remove('hidden');
  }

  showPanel('progress');
  setProgress(0, null);

  const options = {
    doc_type:           document.querySelector('input[name="doc_type"]:checked')?.value || 'auto',
    language:           'auto',
    quality:            'thorough',
    conversion_mode:    document.querySelector('input[name="conversion_mode"]:checked')?.value || 'tag_in_place',
    generate_alt_text:  document.getElementById('alt-text-toggle').checked,
    detect_forms:       true,
  };

  // Process files sequentially
  for (let i = 0; i < selectedFiles.length; i++) {
    if (cancelled) break;
    const { file, id } = selectedFiles[i];

    progressFilename.textContent = selectedFiles.length > 1
      ? `File ${i + 1} of ${selectedFiles.length}: ${file.name}`
      : file.name;

    updateQueueTrackerItem(id, 'processing');
    setProgress(0, null);

    let jobId;
    const t0 = Date.now();   // wall-clock start for accurate elapsed time
    try {
      jobId = await submitFile(file, options);
      fileResults[id] = { jobId, file, status: 'processing' };
    } catch (err) {
      fileResults[id] = { jobId: null, file, status: 'failed', error: err.message };
      updateQueueTrackerItem(id, 'failed');
      if (isSingle) {
        showPanel('result-single');
        showError(err.message);
        return;
      }
      continue;
    }

    const statusData = await pollJob(jobId);
    fileResults[id].statusData = statusData;
    fileResults[id].status = statusData.status;
    fileResults[id].elapsed = ((Date.now() - t0) / 1000).toFixed(1);
    updateQueueTrackerItem(id, statusData.status === 'failed' ? 'failed' : 'done');

    // Fetch veraPDF score for each completed file so the result card can show it
    if (['completed', 'partial'].includes(statusData.status)) {
      try {
        const repRes = await fetch(`/documents/${jobId}/report`, { headers: { 'X-API-Key': API_KEY } });
        if (repRes.ok) {
          const rep = await repRes.json();
          fileResults[id].score = (rep.score !== null && rep.score !== undefined)
            ? Math.round(rep.score * 100) : null;
        }
      } catch { /* score stays null, circle shows status symbol */ }
    }
  }

  if (cancelled) {
    showPanel('upload');
    return;
  }

  if (isSingle) {
    const { id } = selectedFiles[0];
    const r = fileResults[id];
    if (r.status === 'failed') {
      showPanel('result-single');
      showError(r.error || r.statusData?.error_message || 'Conversion failed.');
    } else {
      showPanel('result-single');
      await showSingleResult(id);
    }
  } else {
    showPanel('result-multi');
    renderBatchResults();
  }
}

async function submitFile(file, options) {
  const fd = new FormData();
  fd.append('file', file);
  Object.entries(options).forEach(([k, v]) => fd.append(k, v));

  const res = await fetch('/documents', {
    method: 'POST',
    headers: { 'X-API-Key': API_KEY },
    body: fd,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.message || err.detail || `Upload failed (${res.status})`);
  }
  const data = await res.json();
  return data.job_id;
}

async function pollJob(jobId) {
  let consecutiveErrors = 0;
  return new Promise((resolve) => {
    async function tick() {
      if (cancelled) { resolve({ status: 'cancelled' }); return; }
      try {
        const res = await fetch(`/documents/${jobId}/status`, {
          headers: { 'X-API-Key': API_KEY },
        });
        if (!res.ok) throw new Error(`Status ${res.status}`);
        const data = await res.json();
        consecutiveErrors = 0;
        setProgress(data.progress_pct, data.progress_status);
        if (['completed', 'partial', 'failed'].includes(data.status)) {
          resolve(data); return;
        }
        pollTimer = setTimeout(tick, 1500);
      } catch {
        if (++consecutiveErrors > 5) { resolve({ status: 'failed', error_message: 'Lost connection.' }); return; }
        pollTimer = setTimeout(tick, 2000);
      }
    }
    tick();
  });
}

cancelBtn.addEventListener('click', () => {
  cancelled = true;
  clearTimeout(pollTimer);
  showPanel('upload');
});

// ── Queue tracker (multi-file progress) ───────────────────────────────────
function renderQueueTracker() {
  queueTracker.innerHTML = selectedFiles.map(({ file, id }) => `
    <li class="qt-item" id="qt-${id}" role="listitem">
      <span class="qt-status" data-status="pending" aria-hidden="true">⏳</span>
      <span class="qt-name" title="${escHtml(file.name)}">${escHtml(file.name)}</span>
      <span class="qt-size">${formatBytes(file.size)}</span>
    </li>
  `).join('');
}

function updateQueueTrackerItem(id, state) {
  const el = document.getElementById(`qt-${id}`);
  if (!el) return;
  const statusEl = el.querySelector('.qt-status');
  const icons = { pending: '⏳', processing: '⚙️', done: '✅', failed: '❌' };
  statusEl.textContent = icons[state] || '⏳';
  statusEl.dataset.status = state;
  el.dataset.state = state;
}

// ── Progress display ───────────────────────────────────────────────────────
function setProgress(pct, stage) {
  progressBar.style.width = pct + '%';
  progressPct.textContent = pct + '%';
  progressBarWrap.setAttribute('aria-valuenow', pct);
  STAGE_KEYS.forEach((key, idx) => {
    const el = document.querySelector(`.stage-item[data-stage="${key}"]`);
    if (!el) return;
    el.classList.remove('active', 'done');
    if (stage === key) el.classList.add('active');
    else if (STAGE_KEYS.indexOf(stage) > idx) el.classList.add('done');
  });
}

// ── Single-file result ────────────────────────────────────────────────────
async function showSingleResult(id) {
  const r = fileResults[id];
  const sd = r.statusData;
  const valResult = sd?.validation_result;

  let badgeClass, iconContent, labelText, scoreText;
  if (valResult === 'pass') {
    badgeClass = 'pass'; iconContent = '✓';
    labelText = 'Fully compliant PDF ready'; scoreText = 'PDF/UA-1 validated ✓';
  } else if (!valResult || valResult === 'skipped' || sd?.status === 'partial') {
    badgeClass = 'partial'; iconContent = '~';
    labelText = 'Accessible PDF ready'; scoreText = 'Compliance score pending validation';
  } else {
    badgeClass = 'partial'; iconContent = '!';
    labelText = 'Accessible PDF generated'; scoreText = 'Some compliance issues found';
  }

  resultBadge.className = `result-badge ${badgeClass}`;
  badgeIcon.className = `badge-icon ${badgeClass}`;
  badgeIcon.innerHTML = `<span class="bi-sym">${iconContent}</span>`;
  badgeLabel.textContent = labelText;
  badgeScore.textContent = scoreText;
  resultPages.textContent = sd?.page_count ? `${sd.page_count} page${sd.page_count !== 1 ? 's' : ''}` : '';
  resultTime.textContent  = r.elapsed ? `${r.elapsed}s` : '';

  const stem = r.file.name.includes('.')
    ? r.file.name.slice(0, r.file.name.lastIndexOf('.'))
    : r.file.name;
  const dlFilename = stem + '_ADA_Accessible.pdf';

  downloadBtn.dataset.jobId    = r.jobId;
  downloadBtn.dataset.filename = dlFilename;
  downloadBtn.href = '#';
  downloadBtn.removeAttribute('download');

  pdfPreview.src = `/documents/${r.jobId}/result?api_key=${encodeURIComponent(API_KEY)}&preview=true`;
  fetchReport(r.jobId);
}

downloadBtn.addEventListener('click', async (e) => {
  e.preventDefault();
  await triggerDownload(downloadBtn.dataset.jobId, downloadBtn.dataset.filename, downloadBtn);
});

// ── Batch result ──────────────────────────────────────────────────────────
function renderBatchResults() {
  const total   = selectedFiles.length;
  const success = selectedFiles.filter(({ id }) =>
    ['completed', 'partial'].includes(fileResults[id]?.status)).length;
  const failed  = total - success;

  batchSummary.innerHTML = `
    <div class="batch-stat batch-stat-pass">
      <span class="bs-num">${success}</span>
      <span class="bs-label">converted successfully</span>
    </div>
    ${failed > 0 ? `<div class="batch-stat batch-stat-fail">
      <span class="bs-num">${failed}</span>
      <span class="bs-label">failed</span>
    </div>` : ''}
    <div class="batch-stat">
      <span class="bs-num">${total}</span>
      <span class="bs-label">total files</span>
    </div>
  `;

  resultCards.innerHTML = selectedFiles.map(({ file, id }) => {
    const r = fileResults[id] || {};
    const sd = r.statusData;
    const ok = ['completed', 'partial'].includes(r.status);
    const vr = sd?.validation_result;
    const badgeClass = vr === 'pass' ? 'pass' : (r.status === 'failed' ? 'fail' : 'partial');
    const icon = vr === 'pass' ? '✓' : (r.status === 'failed' ? '✗' : '~');
    const scoreLabel = vr === 'pass' ? 'PDF/UA-1 Pass'
      : (r.status === 'failed' ? 'Failed'
      : 'Partial');
    const stem = file.name.includes('.') ? file.name.slice(0, file.name.lastIndexOf('.')) : file.name;
    const dlName = stem + '_ADA_Accessible.pdf';
    const pages = sd?.page_count
      ? `${sd.page_count} page${sd.page_count !== 1 ? 's' : ''}`
      : '';
    const time = r.elapsed ? `${r.elapsed}s` : '';
    const score = r.score ?? null;   // null means report not yet available

    // Circle: show score % if available, otherwise the status symbol
    const circleInner = score !== null
      ? `<span class="rc-score-num">${score}</span><span class="rc-score-pct">%</span>`
      : `<span style="font-size:1rem">${icon}</span>`;

    // Status tag label — include score if available
    const scoreTagLabel = score !== null
      ? `${score}% PDF/UA`
      : scoreLabel;

    return `
      <li class="result-card ${badgeClass}" role="listitem" data-id="${id}">
        <div class="rc-badge ${badgeClass}" aria-hidden="true">${circleInner}</div>
        <div class="rc-info">
          <p class="rc-name" title="${escHtml(file.name)}">${escHtml(file.name)}</p>
          <p class="rc-meta">
            <span class="rc-status-tag ${badgeClass}">${scoreTagLabel}</span>
            ${pages ? `<span class="rc-chip">${pages}</span>` : ''}
            ${time  ? `<span class="rc-chip">${time}</span>`  : ''}
            ${!ok ? `<span class="rc-error">${escHtml(r.error || sd?.error_message || '')}</span>` : ''}
          </p>
        </div>
        ${ok ? `
          <button class="rc-download-btn" data-job-id="${r.jobId}" data-filename="${escHtml(dlName)}" aria-label="Download ${escHtml(dlName)}">
            <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M3 17a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm3.293-7.707a1 1 0 011.414 0L9 10.586V3a1 1 0 112 0v7.586l1.293-1.293a1 1 0 111.414 1.414l-3 3a1 1 0 01-1.414 0l-3-3a1 1 0 010-1.414z" clip-rule="evenodd"/></svg>
            Download
          </button>` : ''}
      </li>`;
  }).join('');

  // Wire individual download buttons
  resultCards.querySelectorAll('.rc-download-btn').forEach(btn => {
    btn.addEventListener('click', () =>
      triggerDownload(btn.dataset.jobId, btn.dataset.filename, btn));
  });

  // Show/hide Download All
  if (success > 1) {
    downloadAllCount.textContent = success;
    downloadAllBtn.classList.remove('hidden');
  } else {
    downloadAllBtn.classList.add('hidden');
  }
}

downloadAllBtn.addEventListener('click', async () => {
  const jobs = selectedFiles
    .filter(({ id }) => ['completed', 'partial'].includes(fileResults[id]?.status))
    .map(({ file, id }) => {
      const r = fileResults[id];
      const stem = file.name.includes('.') ? file.name.slice(0, file.name.lastIndexOf('.')) : file.name;
      return { jobId: r.jobId, filename: stem + '_ADA_Accessible.pdf' };
    });

  if (!jobs.length) return;

  const origHTML = downloadAllBtn.innerHTML;
  downloadAllBtn.disabled = true;

  try {
    const zip = new JSZip();
    let fetched = 0;

    const setStatus = (msg) => {
      downloadAllBtn.innerHTML = `
        <svg style="width:18px;height:18px;animation:spin 1s linear infinite" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
          <path fill-rule="evenodd" d="M4 2a1 1 0 011 1v2.101a7.002 7.002 0 0111.601 2.566 1 1 0 11-1.885.666A5.002 5.002 0 005.999 7H9a1 1 0 010 2H4a1 1 0 01-1-1V3a1 1 0 011-1zm.008 9.057a1 1 0 011.276.61A5.002 5.002 0 0014.001 13H11a1 1 0 110-2h5a1 1 0 011 1v5a1 1 0 11-2 0v-2.101a7.002 7.002 0 01-11.601-2.566 1 1 0 01.61-1.276z" clip-rule="evenodd"/>
        </svg> ${msg}`;
    };

    setStatus(`Fetching files… (0 / ${jobs.length})`);

    for (const { jobId, filename } of jobs) {
      const res = await fetch(
        `/documents/${jobId}/result?api_key=${encodeURIComponent(API_KEY)}`,
        { headers: { 'X-API-Key': API_KEY } }
      );
      if (res.ok) {
        zip.file(filename, await res.blob());
        fetched++;
        setStatus(`Fetching files… (${fetched} / ${jobs.length})`);
      }
    }

    if (!fetched) { alert('No files could be fetched.'); return; }

    const zipBlob = await zip.generateAsync(
      { type: 'blob', compression: 'DEFLATE', compressionOptions: { level: 6 } },
      ({ percent }) => setStatus(`Compressing… ${Math.round(percent)}%`)
    );

    const url = URL.createObjectURL(zipBlob);
    const a   = document.createElement('a');
    a.href = url;
    a.download = `ADA_Accessible_PDFs_${jobs.length}.zip`;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 15000);

  } catch (err) {
    alert('ZIP failed: ' + err.message);
  } finally {
    downloadAllBtn.disabled = false;
    downloadAllBtn.innerHTML = origHTML;
  }
});

// ── Generic download helper ───────────────────────────────────────────────
async function triggerDownload(jobId, filename, btnEl) {
  if (!jobId) return;
  const origHtml = btnEl?.innerHTML;
  if (btnEl) { btnEl.style.opacity = '0.6'; btnEl.style.pointerEvents = 'none'; }

  try {
    const res = await fetch(`/documents/${jobId}/result?api_key=${encodeURIComponent(API_KEY)}`, {
      headers: { 'X-API-Key': API_KEY },
    });
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    const blob = await res.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  } catch (err) {
    alert('Download failed: ' + err.message);
  } finally {
    if (btnEl) { btnEl.style.opacity = ''; btnEl.style.pointerEvents = ''; if (origHtml) btnEl.innerHTML = origHtml; }
  }
}

// ── Single-file report ────────────────────────────────────────────────────
async function fetchReport(jobId) {
  try {
    const res = await fetch(`/documents/${jobId}/report`, { headers: { 'X-API-Key': API_KEY } });
    if (!res.ok) { reportDetails.style.display = 'none'; return; }
    const report = await res.json();
    const issues = report.failed_assertions;
    reportBadge.textContent = issues === 0 ? '✓ No issues' : `${issues} issue${issues !== 1 ? 's' : ''}`;
    reportBadge.className   = `report-badge ${issues === 0 ? 'ok' : ''}`;
    const pct = report.score !== null ? Math.round(report.score * 100) : null;

    // Update the circle with the actual veraPDF score
    if (pct !== null && !report.parse_error) {
      badgeIcon.innerHTML = `<span class="bi-num">${pct}</span><span class="bi-pct">% PDF/UA</span>`;
    }

    const checksNote = report.failed_checks > 0
      ? ` (${report.failed_checks} occurrence${report.failed_checks !== 1 ? 's' : ''} in document)` : '';
    badgeScore.textContent = report.parse_error
      ? 'Validation not available (veraPDF not installed)'
      : pct !== null
        ? `${pct}% rule compliance · ${issues} rule${issues !== 1 ? 's' : ''} violated${checksNote}`
        : `${issues} issue${issues !== 1 ? 's' : ''} found`;
    reportStats.innerHTML = `
      <div class="stat-box pass-box"><div class="stat-num">${report.passed_assertions}</div><div class="stat-label">Rules passed</div></div>
      <div class="stat-box fail-box"><div class="stat-num">${report.failed_assertions}</div><div class="stat-label">Rules failed</div></div>
      <div class="stat-box"><div class="stat-num">${pct !== null ? pct + '%' : 'N/A'}</div><div class="stat-label">Rule score</div></div>
      <div class="stat-box ${(report.failed_checks || 0) > 0 ? 'fail-box' : 'pass-box'}"><div class="stat-num">${report.failed_checks || 0}</div><div class="stat-label">Failed checks</div></div>
    `;
    if (report.parse_error) {
      reportIssues.innerHTML = `<p style="color:var(--text-soft);font-size:.85rem;padding:8px 0">${report.parse_error}</p>`;
    } else if (report.issues.length === 0) {
      reportIssues.innerHTML = `<p class="no-issues-msg">🎉 All veraPDF accessibility rules passed!</p>`;
    } else {
      const rows = report.issues.map(i => `
        <tr>
          <td><span class="sev-${i.severity}">${i.severity.toUpperCase()}</span></td>
          <td><code style="font-size:.78rem">${escHtml(i.rule_id)}</code></td>
          <td>${escHtml(i.description)}<div class="issue-fix">💡 ${escHtml(i.fix_suggestion)}</div></td>
        </tr>`).join('');
      reportIssues.innerHTML = `
        <table class="issue-table" aria-label="veraPDF accessibility issues">
          <thead><tr><th>Severity</th><th>Rule</th><th>Description &amp; Fix</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
    }

    // ── PAC 2024 section ────────────────────────────────────────────────
    if (report.pac_available) {
      const pacPct = report.pac_score !== null ? Math.round(report.pac_score * 100) : null;
      const pacPass = report.pac_passed;
      pacScoreBadge.textContent = pacPass ? 'PASS' : (pacPct !== null ? `${pacPct}%` : 'FAIL');
      pacScoreBadge.className = `pac-score-badge ${pacPass ? 'pass' : 'fail'}`;
      const failCount = report.pac_failed_checks || 0;
      pacScoreText.textContent = pacPass
        ? 'All PAC 2024 checks passed'
        : `${failCount} check${failCount !== 1 ? 's' : ''} failed · ${pacPct !== null ? pacPct + '% score' : ''}`;
      pacScoreRow.classList.remove('hidden');

      if ((report.pac_issues || []).length > 0) {
        const rows = report.pac_issues.map(i => `
          <tr>
            <td><span class="sev-error">ERROR</span></td>
            <td><code style="font-size:.78rem">${escHtml(i.rule_id)}</code></td>
            <td>${escHtml(i.description)}<div class="issue-fix">💡 ${escHtml(i.fix_suggestion)}</div></td>
          </tr>`).join('');
        pacIssuesEl.innerHTML = `
          <table class="issue-table" aria-label="PAC 2024 issues">
            <thead><tr><th>Severity</th><th>Check</th><th>Description &amp; Fix</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>`;
        pacIssuesWrap.classList.remove('hidden');
      } else if (pacPass) {
        pacIssuesEl.innerHTML = `<p class="no-issues-msg">PAC 2024: all checks passed.</p>`;
        pacIssuesWrap.classList.remove('hidden');
      }
    } else {
      pacScoreRow.classList.add('hidden');
      pacIssuesWrap.classList.add('hidden');
    }
  } catch (_) {
    reportDetails.style.display = 'none';
  }
}

// ── Error (single-file) ───────────────────────────────────────────────────
function showError(msg) {
  errorCard.classList.remove('hidden');
  errorMessage.textContent = msg;
  downloadBtn.closest('.result-actions').classList.add('hidden');
  document.querySelector('.preview-section').classList.add('hidden');
  reportDetails.style.display = 'none';
}

document.getElementById('error-back-btn').addEventListener('click', () => {
  errorCard.classList.add('hidden');
  downloadBtn.closest('.result-actions').classList.remove('hidden');
  document.querySelector('.preview-section').classList.remove('hidden');
  reportDetails.style.display = '';
  resetToUpload();
});

// ── "New conversion" resets ───────────────────────────────────────────────
newConvBtn.addEventListener('click', resetToUpload);
newBatchBtn.addEventListener('click', resetToUpload);

function resetToUpload() {
  selectedFiles = [];
  fileResults   = {};
  cancelled     = false;
  clearTimeout(pollTimer);
  renderFileQueue();
  pdfPreview.src = '';
  reportStats.innerHTML = '';
  reportIssues.innerHTML = '';
  pacScoreRow.classList.add('hidden');
  pacIssuesWrap.classList.add('hidden');
  pacIssuesEl.innerHTML = '';
  document.querySelectorAll('.meta-chip').forEach(c => c.textContent = '');
  setProgress(0, null);
  queueTracker.innerHTML = '';
  queueTracker.classList.add('hidden');
  errorCard.classList.add('hidden');
  downloadBtn.closest('.result-actions')?.classList.remove('hidden');
  document.querySelector('.preview-section')?.classList.remove('hidden');
  reportDetails.style.display = '';
  resultCards.innerHTML = '';
  batchSummary.innerHTML = '';
  downloadAllBtn.classList.add('hidden');
  showPanel('upload');
}

// ── Panel switching ───────────────────────────────────────────────────────
function showPanel(name) {
  uploadPanel.classList.toggle('hidden',        name !== 'upload');
  progressPanel.classList.toggle('hidden',      name !== 'progress');
  resultPanelSingle.classList.toggle('hidden',  name !== 'result-single');
  resultPanelMulti.classList.toggle('hidden',   name !== 'result-multi');
}

// ── Helpers ───────────────────────────────────────────────────────────────
function formatBytes(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
