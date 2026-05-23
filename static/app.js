/* ================================================================
   HQC × AES-256-GCM Hybrid Demo — Frontend Logic
   ================================================================ */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
    currentPhase: -1,     // -1 = not started
    completedPhases: [],
    running: false,
    // Data from each phase
    pk: null,
    sk: null,
    K: null,
    c_kem: null,
    encrypted: null,
    K_prime: null,
    decrypted: null,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function truncateHex(hex, maxLen = 24) {
    if (!hex) return '—';
    const str = String(hex);
    if (str.length <= maxLen) return str;
    const half = Math.floor((maxLen - 3) / 2);
    return str.slice(0, half + 2) + '…' + str.slice(-half);
}

function formatHexArray(arr, maxItems = 3) {
    if (!arr || arr.length === 0) return '—';
    const shown = arr.slice(0, maxItems).map(h => truncateHex(h, 20));
    const suffix = arr.length > maxItems ? `\n… (${arr.length} total)` : '';
    return shown.join('\n') + suffix;
}

function nowTime() {
    return new Date().toLocaleTimeString('en-GB', { hour12: false });
}

// ---------------------------------------------------------------------------
// Toast notifications
// ---------------------------------------------------------------------------
function showToast(message, type = 'error') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(-10px)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ---------------------------------------------------------------------------
// Status indicator
// ---------------------------------------------------------------------------
function setStatus(text, mode = 'idle') {
    document.getElementById('status-dot').className = `status-dot ${mode}`;
    document.getElementById('status-text').textContent = text;
}

// ---------------------------------------------------------------------------
// Phase stepper
// ---------------------------------------------------------------------------
function updateStepper(activePhase) {
    for (let i = 0; i <= 5; i++) {
        const step = document.getElementById(`step-${i}`);
        step.classList.remove('active', 'completed');
        if (state.completedPhases.includes(i)) {
            step.classList.add('completed');
            step.querySelector('.stepper-dot').textContent = '✓';
        } else if (i === activePhase) {
            step.classList.add('active');
            step.querySelector('.stepper-dot').textContent = i;
        } else {
            step.querySelector('.stepper-dot').textContent = i;
        }
    }
}

// ---------------------------------------------------------------------------
// Button states
// ---------------------------------------------------------------------------
function setButtonRunning(phase, running) {
    const btn = document.getElementById(`btn-phase-${phase}`);
    if (running) {
        btn.classList.add('running');
    } else {
        btn.classList.remove('running');
    }
}

function enableNextButtons() {
    const maxCompleted = Math.max(-1, ...state.completedPhases);
    for (let i = 0; i <= 5; i++) {
        const btn = document.getElementById(`btn-phase-${i}`);
        btn.disabled = state.running || (i > maxCompleted + 1);
    }
    document.getElementById('btn-run-all').disabled = state.running;
}

// ---------------------------------------------------------------------------
// Channel log
// ---------------------------------------------------------------------------
function clearChannelLog() {
    document.getElementById('channel-log').innerHTML = '';
}

function appendChannelLog(message, icon = '📨') {
    const log = document.getElementById('channel-log');
    // Remove placeholder if present
    const placeholder = log.querySelector('.text-center');
    if (placeholder) placeholder.remove();

    const entry = document.createElement('div');
    entry.className = 'log-entry';
    entry.innerHTML = `
        <span class="log-time">${nowTime()}</span>
        <span class="log-icon">${icon}</span>
        <span class="log-msg">${message}</span>
    `;
    log.appendChild(entry);
    log.scrollTop = log.scrollHeight;
}

// ---------------------------------------------------------------------------
// Show phase content
// ---------------------------------------------------------------------------
function showContent(phase) {
    const el = document.getElementById(`content-phase-${phase}`);
    if (el) el.classList.add('visible');
}

function showTimingBadge(containerId, ms, label = 'HW') {
    const el = document.getElementById(containerId);
    if (el) {
        el.innerHTML = `<span class="timing-badge">⏱ ${label}: ${ms.toFixed(1)} ms</span>`;
    }
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------
async function apiCall(url, body = {}) {
    const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok || data.error) {
        throw new Error(data.error || `HTTP ${resp.status}`);
    }
    return data;
}

// ---------------------------------------------------------------------------
// Phase 0: KeyGen (Alice)
// ---------------------------------------------------------------------------
async function runPhase0() {
    appendChannelLog('Alice bắt đầu sinh khóa trên FPGA…', '🔑');

    const data = await apiCall('/api/keygen', {});

    state.pk = data.pk;
    state.sk = data.sk;

    // Update Alice panel
    document.getElementById('alice-pk').textContent =
        `h: ${formatHexArray(data.pk.h)}\ns: ${formatHexArray(data.pk.s)}`;
    document.getElementById('alice-sk').textContent =
        `x: ${formatHexArray(data.sk.x, 4)}\ny: ${formatHexArray(data.sk.y, 4)}`;
    showContent(0);
    showTimingBadge('timing-phase-0', data.hw_time_ms, 'FPGA');

    appendChannelLog(`KeyGen hoàn tất — ${data.hw_time_ms.toFixed(1)}ms`, '✅');
    appendChannelLog(`pk = {h, s} được gửi công khai cho Bob`, '📤');
}

// ---------------------------------------------------------------------------
// Phase 1: KEM Encap (Bob)
// ---------------------------------------------------------------------------
async function runPhase1() {
    appendChannelLog('Bob nhận pk từ Alice, bắt đầu Encapsulation…', '📥');

    const data = await apiCall('/api/encap', {});

    state.K = data.K;
    state.c_kem = data.c_kem;

    document.getElementById('bob-K').textContent = truncateHex(data.K, 66);
    document.getElementById('bob-ckem').textContent =
        `u: ${formatHexArray(data.c_kem.u)}\nv: ${formatHexArray(data.c_kem.v)}\nd: ${formatHexArray(data.c_kem.d)}`;
    showContent(1);
    showTimingBadge('timing-phase-1', data.hw_time_ms, 'FPGA');

    appendChannelLog(`Encap hoàn tất — K đã được sinh — ${data.hw_time_ms.toFixed(1)}ms`, '✅');
}

// ---------------------------------------------------------------------------
// Phase 2: AES-GCM Encrypt (Bob — Software)
// ---------------------------------------------------------------------------
async function runPhase2() {
    appendChannelLog('Bob mã hóa ảnh meo.png bằng AES-256-GCM…', '🔒');

    const data = await apiCall('/api/encrypt', {});

    state.encrypted = {
        data_b64: data.encrypted_b64,
        nonce_hex: data.nonce_hex,
        tag_hex: data.tag_hex,
    };

    // Show original image
    const origContainer = document.getElementById('img-original-container');
    origContainer.style.display = 'block';
    document.getElementById('img-original').src =
        'data:image/png;base64,' + data.original_preview_b64;

    document.getElementById('bob-nonce').textContent = data.nonce_hex;
    document.getElementById('bob-tag').textContent = data.tag_hex;
    showContent(2);
    showTimingBadge('timing-phase-2', data.sw_time_ms, 'CPU');

    appendChannelLog(`AES-GCM Encrypt hoàn tất — ${data.file_size_bytes} bytes → ciphertext`, '✅');
    appendChannelLog(`Nonce: ${truncateHex(data.nonce_hex, 30)}`, '🔢');
    appendChannelLog(`Tag: ${truncateHex(data.tag_hex, 36)}`, '🏷️');
}

// ---------------------------------------------------------------------------
// Phase 3: Transmission Simulation (UI only)
// ---------------------------------------------------------------------------
async function runPhase3() {
    const items = [
        { label: 'c_kem (u, v, d)', icon: '📦', delay: 600 },
        { label: 'encrypted_image', icon: '🖼️', delay: 800 },
        { label: 'nonce (12 bytes)', icon: '🔢', delay: 400 },
        { label: 'auth_tag (16 bytes)', icon: '🏷️', delay: 400 },
    ];

    appendChannelLog('═══ Bắt đầu truyền qua kênh công khai ═══', '📡');

    for (const item of items) {
        await new Promise(r => setTimeout(r, item.delay));
        appendChannelLog(`→ Gửi: ${item.label}`, item.icon);
    }

    await new Promise(r => setTimeout(r, 500));
    appendChannelLog('═══ Truyền tải hoàn tất ═══', '✅');
    appendChannelLog('Alice nhận được gói dữ liệu', '📥');
}

// ---------------------------------------------------------------------------
// Phase 4: KEM Decap (Alice)
// ---------------------------------------------------------------------------
async function runPhase4() {
    appendChannelLog('Alice thực hiện Decapsulation trên FPGA…', '🔓');

    const data = await apiCall('/api/decap', {});

    state.K_prime = data.K_prime;

    document.getElementById('alice-Kprime').textContent = truncateHex(data.K_prime, 66);

    const matchEl = document.getElementById('alice-match');
    if (data.keys_match) {
        matchEl.innerHTML = '<span class="match-badge success">✅ K\' = K — Khóa khớp!</span>';
    } else {
        matchEl.innerHTML = '<span class="match-badge fail">❌ K\' ≠ K — Khóa KHÔNG khớp!</span>';
    }

    showContent(4);
    showTimingBadge('timing-phase-4', data.hw_time_ms, 'FPGA');

    appendChannelLog(`Decap hoàn tất — K' recovered — ${data.hw_time_ms.toFixed(1)}ms`, '✅');
    appendChannelLog(data.keys_match ? 'K\' = K ✅ Khóa khớp!' : 'K\' ≠ K ❌ Khóa KHÔNG khớp!',
        data.keys_match ? '✅' : '❌');
}

// ---------------------------------------------------------------------------
// Phase 5: AES-GCM Decrypt (Alice — Software)
// ---------------------------------------------------------------------------
async function runPhase5() {
    appendChannelLog('Alice giải mã ảnh bằng AES-256-GCM với K\'…', '🔓');

    const data = await apiCall('/api/decrypt', {});

    state.decrypted = data;

    // Show decrypted image
    const decContainer = document.getElementById('img-decrypted-container');
    decContainer.style.display = 'block';
    document.getElementById('img-decrypted').src =
        'data:image/png;base64,' + data.decrypted_image_b64;

    const tagEl = document.getElementById('alice-tag-status');
    if (data.tag_valid) {
        tagEl.innerHTML = '<span class="match-badge success">✅ Tag xác thực thành công — Dữ liệu nguyên vẹn</span>';
    } else {
        tagEl.innerHTML = '<span class="match-badge fail">❌ Tag KHÔNG hợp lệ — Dữ liệu bị thay đổi!</span>';
    }

    showContent(5);
    showTimingBadge('timing-phase-5', data.sw_time_ms, 'CPU');

    appendChannelLog(`AES-GCM Decrypt hoàn tất — ảnh khôi phục thành công`, '✅');
    appendChannelLog(`Tag valid: ${data.tag_valid ? '✅ Yes' : '❌ No'}`, '🏷️');
    appendChannelLog('═══ Demo hoàn tất ═══', '🎉');
}

// ---------------------------------------------------------------------------
// Phase dispatcher
// ---------------------------------------------------------------------------
const phaseRunners = [runPhase0, runPhase1, runPhase2, runPhase3, runPhase4, runPhase5];

async function runPhase(phase) {
    if (state.running) return;

    state.running = true;
    state.currentPhase = phase;
    setStatus(`Running Phase ${phase}…`, 'running');
    setButtonRunning(phase, true);
    updateStepper(phase);
    enableNextButtons();

    try {
        await phaseRunners[phase]();
        state.completedPhases.push(phase);
        updateStepper(-1);
        showToast(`Phase ${phase} completed!`, 'success');
    } catch (err) {
        setStatus(`Phase ${phase} failed`, 'error');
        showToast(`Phase ${phase}: ${err.message}`, 'error');
        appendChannelLog(`❌ Error: ${err.message}`, '🚫');
    } finally {
        state.running = false;
        state.currentPhase = -1;
        setButtonRunning(phase, false);
        enableNextButtons();
        if (!document.querySelector('.status-dot.error')) {
            setStatus('Idle', 'idle');
        }
    }
}

// ---------------------------------------------------------------------------
// Run All
// ---------------------------------------------------------------------------
async function runAll() {
    if (state.running) return;

    clearChannelLog();
    appendChannelLog('═══ Bắt đầu Demo tự động (Phase 0 → 5) ═══', '⚡');

    for (let i = 0; i <= 5; i++) {
        if (state.completedPhases.includes(i)) continue;
        await runPhase(i);
        // Check if an error stopped execution
        if (!state.completedPhases.includes(i)) break;
        // Small delay between phases for visual effect
        if (i < 5) await new Promise(r => setTimeout(r, 300));
    }
}

// ---------------------------------------------------------------------------
// Reset
// ---------------------------------------------------------------------------
async function resetDemo() {
    try {
        await fetch('/api/reset', { method: 'POST' });
    } catch (_) { /* ignore */ }

    // Reset state
    state.currentPhase = -1;
    state.completedPhases = [];
    state.running = false;
    state.pk = null;
    state.sk = null;
    state.K = null;
    state.c_kem = null;
    state.encrypted = null;
    state.K_prime = null;
    state.decrypted = null;

    // Reset UI
    updateStepper(-1);
    enableNextButtons();
    setStatus('Idle', 'idle');

    // Hide all phase contents
    document.querySelectorAll('.phase-content').forEach(el => el.classList.remove('visible'));

    // Reset hex displays
    ['bob-K', 'bob-ckem', 'bob-nonce', 'bob-tag', 'alice-pk', 'alice-sk', 'alice-Kprime']
        .forEach(id => { document.getElementById(id).textContent = '—'; });

    // Reset match/tag badges
    ['alice-match', 'alice-tag-status', 'timing-phase-0', 'timing-phase-1',
     'timing-phase-2', 'timing-phase-4', 'timing-phase-5']
        .forEach(id => { document.getElementById(id).innerHTML = ''; });

    // Hide images
    document.getElementById('img-original-container').style.display = 'none';
    document.getElementById('img-decrypted-container').style.display = 'none';

    // Reset channel log
    clearChannelLog();
    document.getElementById('channel-log').innerHTML =
        '<div class="text-center text-gray-600 text-xs py-8">Chờ demo bắt đầu…</div>';

    showToast('Demo state cleared', 'success');
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
    updateStepper(-1);
    enableNextButtons();
});
