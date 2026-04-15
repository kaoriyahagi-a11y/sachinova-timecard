// ━━━━━ パスワード表示/非表示 ━━━━━
const EYE_OPEN = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>`;
const EYE_CLOSED = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>`;

document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.password-toggle').forEach(function(btn) {
        btn.innerHTML = EYE_CLOSED;
        btn.addEventListener('click', function() {
            const input = this.parentElement.querySelector('input');
            if (input.type === 'password') {
                input.type = 'text';
                this.innerHTML = EYE_OPEN;
            } else {
                input.type = 'password';
                this.innerHTML = EYE_CLOSED;
            }
        });
    });
});

// ━━━━━ 確認音（ピッ） ━━━━━
function playBeep() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.frequency.value = 880;
        osc.type = 'sine';
        gain.gain.setValueAtTime(0.3, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + 0.3);
    } catch(e) {}
}

// ━━━━━ 時計更新ユーティリティ ━━━━━
function startClock(timeElId, dateElId) {
    function tick() {
        const now = new Date();
        if (timeElId) {
            const el = document.getElementById(timeElId);
            if (el) el.textContent = now.toLocaleTimeString('ja-JP', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
        }
        if (dateElId) {
            const el = document.getElementById(dateElId);
            if (el) el.textContent = now.toLocaleDateString('ja-JP', {year:'numeric', month:'long', day:'numeric', weekday:'long'});
        }
    }
    tick();
    setInterval(tick, 1000);
}
