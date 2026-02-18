// ***************************************************************
// RETRO SFX ENGINE — Procedural 8-bit sounds via Web Audio API
// ***************************************************************
//
// All sounds are synthesized in real-time. No MP3s needed.
// A global "telephone line" bandpass filter gives everything
// that crunchy, lo-fi retro feel.
//
// Usage:
//   RetroSFX.click()           — typewriter key (buttons, tabs)
//   RetroSFX.computeStart()    — start rapid data blips (returns stop fn)
//   RetroSFX.modemHandshake()  — 56k dial-up sequence (~5s)
//   RetroSFX.successChime()    — rising arpeggio
//   RetroSFX.alertBuzz()       — warning buzz
//   RetroSFX.powerUp()         — page load sweep
// ***************************************************************

const RetroSFX = (() => {
    let ctx = null;
    let masterFilter = null;
    let masterGain = null;
    let initialized = false;
    let muted = false;

    // ── Lazy init (requires user gesture) ─────────────────────
    const init = () => {
        if (initialized) return true;
        try {
            ctx = new (window.AudioContext || window.webkitAudioContext)();

            // "Telephone line" bandpass — makes everything sound retro
            masterFilter = ctx.createBiquadFilter();
            masterFilter.type = "bandpass";
            masterFilter.frequency.value = 2000;
            masterFilter.Q.value = 0.7;

            // Compressor to tame peaks
            const compressor = ctx.createDynamicsCompressor();
            compressor.threshold.value = -20;
            compressor.ratio.value = 4;

            // Master volume
            masterGain = ctx.createGain();
            masterGain.gain.value = 0.3;

            masterFilter.connect(compressor);
            compressor.connect(masterGain);
            masterGain.connect(ctx.destination);

            initialized = true;
            console.log("[RetroSFX] Initialized — Web Audio API ready");
            return true;
        } catch (e) {
            console.warn("[RetroSFX] Web Audio API not available:", e);
            return false;
        }
    };

    // ── Helper: connect node → master chain ───────────────────
    const toMaster = (node) => {
        node.connect(masterFilter);
        return node;
    };

    // ── Helper: create white noise buffer ─────────────────────
    const noiseBuffer = (duration) => {
        const len = ctx.sampleRate * duration;
        const buf = ctx.createBuffer(1, len, ctx.sampleRate);
        const data = buf.getChannelData(0);
        for (let i = 0; i < len; i++) {
            data[i] = Math.random() * 2 - 1;
        }
        return buf;
    };

    // ═══════════════════════════════════════════════════════════
    // 1. TYPEWRITER CLICK — Sharp metallic clack + paper thud
    // ═══════════════════════════════════════════════════════════
    const click = () => {
        if (!init() || muted) return;
        const t = ctx.currentTime;

        // The "Clack" — square wave pitch drop
        const osc = ctx.createOscillator();
        const oscGain = ctx.createGain();
        osc.type = "square";
        osc.frequency.setValueAtTime(1200, t);
        osc.frequency.exponentialRampToValueAtTime(150, t + 0.04);
        oscGain.gain.setValueAtTime(0.4, t);
        oscGain.gain.exponentialRampToValueAtTime(0.001, t + 0.04);
        osc.connect(oscGain);
        toMaster(oscGain);
        osc.start(t);
        osc.stop(t + 0.05);

        // The "Thud" — white noise burst
        const noise = ctx.createBufferSource();
        const noiseGain = ctx.createGain();
        noise.buffer = noiseBuffer(0.06);
        noiseGain.gain.setValueAtTime(0.5, t);
        noiseGain.gain.exponentialRampToValueAtTime(0.001, t + 0.06);
        noise.connect(noiseGain);
        toMaster(noiseGain);
        noise.start(t);
    };

    // ═══════════════════════════════════════════════════════════
    // 2. DATA COMPUTE BLIPS — Random sawtooth arpeggios
    //    Returns a stop() function to cancel the loop
    // ═══════════════════════════════════════════════════════════
    const computeStart = () => {
        if (!init() || muted) return () => { };
        let running = true;

        const blip = () => {
            if (!running) return;
            const t = ctx.currentTime;
            const freq = 600 + Math.random() * 1800;

            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = "sawtooth";
            osc.frequency.setValueAtTime(freq, t);
            osc.frequency.exponentialRampToValueAtTime(freq * 0.3, t + 0.06);
            gain.gain.setValueAtTime(0.12, t);
            gain.gain.exponentialRampToValueAtTime(0.001, t + 0.06);
            osc.connect(gain);
            toMaster(gain);
            osc.start(t);
            osc.stop(t + 0.07);

            if (running) {
                setTimeout(blip, 60 + Math.random() * 80);
            }
        };

        blip();
        return () => { running = false; };
    };

    // ═══════════════════════════════════════════════════════════
    // 3. MODEM HANDSHAKE — Full 56k dial-up sequence (~5s)
    //    Dial tone → Answer tone → FSK static
    // ═══════════════════════════════════════════════════════════
    const modemHandshake = () => {
        if (!init() || muted) return;
        const t = ctx.currentTime;

        // Phase 1: Dial tone (US standard 350Hz + 440Hz) — 1.2s
        const dt1 = ctx.createOscillator();
        const dt2 = ctx.createOscillator();
        dt1.frequency.value = 350;
        dt2.frequency.value = 440;
        const dialGain = ctx.createGain();
        dialGain.gain.setValueAtTime(0.15, t);
        dialGain.gain.setValueAtTime(0, t + 1.2);
        dt1.connect(dialGain);
        dt2.connect(dialGain);
        toMaster(dialGain);
        dt1.start(t);
        dt2.start(t);
        dt1.stop(t + 1.3);
        dt2.stop(t + 1.3);

        // Phase 2: Answer tone (2100Hz V.25 handshake) — 1.2s
        const ans = ctx.createOscillator();
        ans.frequency.value = 2100;
        const ansGain = ctx.createGain();
        ansGain.gain.setValueAtTime(0, t + 1.3);
        ansGain.gain.linearRampToValueAtTime(0.25, t + 1.5);
        ansGain.gain.setValueAtTime(0.25, t + 2.3);
        ansGain.gain.linearRampToValueAtTime(0, t + 2.5);
        ans.connect(ansGain);
        toMaster(ansGain);
        ans.start(t + 1.3);
        ans.stop(t + 2.6);

        // Phase 3: FSK data static — amplitude-modulated noise — 2s
        const noiseLen = 2.0;
        const buf = ctx.createBuffer(1, ctx.sampleRate * noiseLen, ctx.sampleRate);
        const data = buf.getChannelData(0);
        for (let i = 0; i < buf.length; i++) {
            // AM modulation to sound like bitstreams
            const mod = Math.sin(i * 0.008) > 0 ? 1 : 0.15;
            data[i] = (Math.random() * 2 - 1) * mod;
        }
        const staticNode = ctx.createBufferSource();
        staticNode.buffer = buf;
        const staticGain = ctx.createGain();
        staticGain.gain.setValueAtTime(0, t + 2.6);
        staticGain.gain.linearRampToValueAtTime(0.3, t + 2.8);
        staticGain.gain.linearRampToValueAtTime(0.15, t + 4.0);
        staticGain.gain.linearRampToValueAtTime(0, t + 4.6);
        staticNode.connect(staticGain);
        toMaster(staticGain);
        staticNode.start(t + 2.6);
    };

    // ═══════════════════════════════════════════════════════════
    // 4. SUCCESS CHIME — Rising arpeggio G4→C5→E5
    // ═══════════════════════════════════════════════════════════
    const successChime = () => {
        if (!init() || muted) return;
        const t = ctx.currentTime;
        const notes = [392, 523.25, 659.25]; // G4, C5, E5

        notes.forEach((freq, i) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = "triangle";
            osc.frequency.value = freq;
            const start = t + i * 0.12;
            gain.gain.setValueAtTime(0.3, start);
            gain.gain.exponentialRampToValueAtTime(0.001, start + 0.25);
            osc.connect(gain);
            toMaster(gain);
            osc.start(start);
            osc.stop(start + 0.3);
        });
    };

    // ═══════════════════════════════════════════════════════════
    // 5. ALERT BUZZ — Low warning buzz (destructive actions)
    // ═══════════════════════════════════════════════════════════
    const alertBuzz = () => {
        if (!init() || muted) return;
        const t = ctx.currentTime;

        // Two-tone descending buzz
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "square";
        osc.frequency.setValueAtTime(220, t);
        osc.frequency.setValueAtTime(165, t + 0.12);
        gain.gain.setValueAtTime(0.25, t);
        gain.gain.setValueAtTime(0.25, t + 0.2);
        gain.gain.exponentialRampToValueAtTime(0.001, t + 0.3);
        osc.connect(gain);
        toMaster(gain);
        osc.start(t);
        osc.stop(t + 0.35);
    };

    // ═══════════════════════════════════════════════════════════
    // 6. POWER UP — Rising frequency sweep on page load
    // ═══════════════════════════════════════════════════════════
    const powerUp = () => {
        if (!init() || muted) return;
        const t = ctx.currentTime;

        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sawtooth";
        osc.frequency.setValueAtTime(60, t);
        osc.frequency.exponentialRampToValueAtTime(800, t + 0.6);
        gain.gain.setValueAtTime(0.2, t);
        gain.gain.setValueAtTime(0.2, t + 0.4);
        gain.gain.exponentialRampToValueAtTime(0.001, t + 0.7);
        osc.connect(gain);
        toMaster(gain);
        osc.start(t);
        osc.stop(t + 0.8);

        // Little "ding" at the end
        const ding = ctx.createOscillator();
        const dingGain = ctx.createGain();
        ding.type = "triangle";
        ding.frequency.value = 880;
        dingGain.gain.setValueAtTime(0, t + 0.55);
        dingGain.gain.linearRampToValueAtTime(0.25, t + 0.6);
        dingGain.gain.exponentialRampToValueAtTime(0.001, t + 1.0);
        ding.connect(dingGain);
        toMaster(dingGain);
        ding.start(t + 0.55);
        ding.stop(t + 1.1);
    };

    // ── Volume / Mute controls ────────────────────────────────
    const setVolume = (v) => {
        if (masterGain) masterGain.gain.value = Math.max(0, Math.min(1, v));
    };

    const toggleMute = () => {
        muted = !muted;
        console.log(`[RetroSFX] ${muted ? "Muted" : "Unmuted"}`);
        return muted;
    };

    const isMuted = () => muted;

    // ── Public API ────────────────────────────────────────────
    return {
        init,
        click,
        computeStart,
        modemHandshake,
        successChime,
        alertBuzz,
        powerUp,
        setVolume,
        toggleMute,
        isMuted,
    };
})();
