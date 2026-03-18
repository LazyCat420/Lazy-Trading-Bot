// ***************************************************************
// SYNTHWAVE SFX ENGINE — Warm, smooth 80s synth sounds
// ***************************************************************
//
// All sounds use:
//   - Low frequencies (80-500Hz base, warm range)
//   - Sine & triangle waves only (no harsh sawtooth/square)
//   - Slow attack/release (no sharp transients)
//   - Gentle detuning for analog warmth
//   - Sub-bass undertones for body
//
// Public API:
//   RetroSFX.init()           — lazy-init AudioContext
//   RetroSFX.click()          — soft UI tap
//   RetroSFX.computeStart()   — ambient pad (returns stop fn)
//   RetroSFX.modemHandshake() — warm launch sequence
//   RetroSFX.successChime()   — gentle success arpeggio
//   RetroSFX.alertBuzz()      — smooth low warning
//   RetroSFX.powerUp()        — warm power sweep
// ***************************************************************

const RetroSFX = (() => {
    let ctx = null;
    let masterGain = null;
    let initialized = false;
    let muted = false;

    // ── Lazy init (requires user gesture) ─────────────────────
    function init() {
        if (initialized) return;
        try {
            ctx = new (window.AudioContext || window.webkitAudioContext)();
            masterGain = ctx.createGain();
            masterGain.gain.value = 0.35; // Keep overall volume gentle
            masterGain.connect(ctx.destination);
            initialized = true;
        } catch (e) {
            console.warn("[SFX] AudioContext failed:", e);
        }
    }

    // ── Helper: connect to master ─────────────────────────────
    function toMaster(node) {
        node.connect(masterGain);
        return node;
    }

    // ── Helper: create a warm oscillator with slow fade ────────
    function warmOsc(freq, type, startTime, duration, volume = 0.15) {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = type;
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0, startTime);
        gain.gain.linearRampToValueAtTime(volume, startTime + Math.min(0.08, duration * 0.3));
        gain.gain.setValueAtTime(volume, startTime + duration * 0.6);
        gain.gain.linearRampToValueAtTime(0, startTime + duration);
        osc.connect(gain);
        toMaster(gain);
        osc.start(startTime);
        osc.stop(startTime + duration + 0.05);
        return osc;
    }

    // ═══════════════════════════════════════════════════════════
    // 1. SOFT TAP — Gentle low sine blip
    //    A warm "tok" sound, like tapping glass underwater
    // ═══════════════════════════════════════════════════════════
    function click() {
        init();
        if (muted || !ctx) return;
        const t = ctx.currentTime;

        // Soft sine tap at 280Hz
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.setValueAtTime(280, t);
        osc.frequency.exponentialRampToValueAtTime(180, t + 0.08);
        gain.gain.setValueAtTime(0.12, t);
        gain.gain.exponentialRampToValueAtTime(0.001, t + 0.1);
        osc.connect(gain);
        toMaster(gain);
        osc.start(t);
        osc.stop(t + 0.12);

        // Tiny sub-bass thud for warmth
        const sub = ctx.createOscillator();
        const subG = ctx.createGain();
        sub.type = "sine";
        sub.frequency.value = 100;
        subG.gain.setValueAtTime(0.06, t);
        subG.gain.exponentialRampToValueAtTime(0.001, t + 0.06);
        sub.connect(subG);
        toMaster(subG);
        sub.start(t);
        sub.stop(t + 0.08);
    }

    // ═══════════════════════════════════════════════════════════
    // 2. AMBIENT PAD — Slow-breathing warm drone
    //    Plays during loading/compute. Returns stop() function.
    // ═══════════════════════════════════════════════════════════
    function computeStart() {
        init();
        if (muted || !ctx) return () => { };

        const t = ctx.currentTime;
        const nodes = [];

        // Deep warm pad: C3 (130Hz) + E3 (165Hz) detuned pair
        [130.81, 164.81].forEach(freq => {
            const osc1 = ctx.createOscillator();
            const osc2 = ctx.createOscillator();
            const gain = ctx.createGain();

            osc1.type = "sine";
            osc2.type = "triangle";
            osc1.frequency.value = freq;
            osc2.frequency.value = freq + 1.5; // Slow beat frequency

            gain.gain.setValueAtTime(0, t);
            gain.gain.linearRampToValueAtTime(0.06, t + 1.5);

            osc1.connect(gain);
            osc2.connect(gain);
            toMaster(gain);

            osc1.start(t);
            osc2.start(t);
            nodes.push({ osc1, osc2, gain });
        });

        // Gentle LFO shimmer on the pad
        const lfo = ctx.createOscillator();
        const lfoGain = ctx.createGain();
        lfo.type = "sine";
        lfo.frequency.value = 0.3; // Very slow breathing
        lfoGain.gain.value = 8; // Subtle pitch wobble
        lfo.connect(lfoGain);
        nodes.forEach(n => {
            lfoGain.connect(n.osc1.frequency);
        });
        lfo.start(t);

        // Periodic soft blip every ~3s
        let blipTimer = null;
        let running = true;
        function scheduleBlip() {
            if (!running || !ctx) return;
            const now = ctx.currentTime;
            warmOsc(220, "sine", now, 0.3, 0.04);
            warmOsc(330, "sine", now + 0.15, 0.25, 0.02);
            blipTimer = setTimeout(scheduleBlip, 3000 + Math.random() * 1500);
        }
        blipTimer = setTimeout(scheduleBlip, 2000);

        // Return stop function
        return function stop() {
            running = false;
            if (blipTimer) clearTimeout(blipTimer);
            const now = ctx.currentTime;
            nodes.forEach(n => {
                n.gain.gain.linearRampToValueAtTime(0, now + 0.8);
                try { n.osc1.stop(now + 1); } catch { }
                try { n.osc2.stop(now + 1); } catch { }
            });
            try { lfo.stop(now + 1); } catch { }
        };
    }

    // ═══════════════════════════════════════════════════════════
    // 3. SYNTHWAVE LAUNCH — Warm pad + gentle rising arpeggio
    //    Plays when starting a bot run (~3s)
    // ═══════════════════════════════════════════════════════════
    function modemHandshake() {
        init();
        if (muted || !ctx) return;
        const t = ctx.currentTime;

        // ── Deep warm pad (C2 + G2) ──
        const padFreqs = [65.41, 98.0]; // C2, G2
        padFreqs.forEach(freq => {
            const osc = ctx.createOscillator();
            const osc2 = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = "sine";
            osc2.type = "triangle";
            osc.frequency.value = freq;
            osc2.frequency.value = freq * 1.002; // Warm detune
            gain.gain.setValueAtTime(0, t);
            gain.gain.linearRampToValueAtTime(0.10, t + 0.6);
            gain.gain.setValueAtTime(0.10, t + 2.2);
            gain.gain.linearRampToValueAtTime(0, t + 3.5);
            osc.connect(gain);
            osc2.connect(gain);
            toMaster(gain);
            osc.start(t);
            osc2.start(t);
            osc.stop(t + 3.8);
            osc2.stop(t + 3.8);
        });

        // ── Gentle rising arpeggio (Cmaj7 voiced low) ──
        // C3, E3, G3, B3, C4 — all sine, gentle
        const arpNotes = [130.81, 164.81, 196.0, 246.94, 261.63];
        arpNotes.forEach((freq, i) => {
            const delay = 0.4 + i * 0.35;
            warmOsc(freq, "sine", t + delay, 0.8, 0.07);
            // Ghost triangle harmonic one octave up, very quiet
            warmOsc(freq * 2, "triangle", t + delay + 0.05, 0.5, 0.015);
        });

        // ── Sub-bass swell underneath ──
        const sub = ctx.createOscillator();
        const subG = ctx.createGain();
        sub.type = "sine";
        sub.frequency.value = 55; // A1 — deep sub
        subG.gain.setValueAtTime(0, t);
        subG.gain.linearRampToValueAtTime(0.08, t + 1.0);
        subG.gain.setValueAtTime(0.08, t + 2.5);
        subG.gain.linearRampToValueAtTime(0, t + 3.5);
        sub.connect(subG);
        toMaster(subG);
        sub.start(t);
        sub.stop(t + 3.8);
    }

    // ═══════════════════════════════════════════════════════════
    // 4. SUCCESS CHIME — Warm pentatonic arpeggio
    //    Gentle ascending notes: G3, A3, B3, D4, G4
    // ═══════════════════════════════════════════════════════════
    function successChime() {
        init();
        if (muted || !ctx) return;
        const t = ctx.currentTime;

        // Pentatonic ascent — warm and resolved
        const notes = [196.0, 220.0, 246.94, 293.66, 392.0]; // G3 A3 B3 D4 G4
        notes.forEach((freq, i) => {
            const delay = i * 0.12;
            warmOsc(freq, "sine", t + delay, 0.45, 0.09);
            // Soft triangle echo
            warmOsc(freq, "triangle", t + delay + 0.06, 0.3, 0.025);
        });

        // Gentle low pad underneath
        const pad = ctx.createOscillator();
        const padG = ctx.createGain();
        pad.type = "sine";
        pad.frequency.value = 98.0; // G2
        padG.gain.setValueAtTime(0, t);
        padG.gain.linearRampToValueAtTime(0.05, t + 0.15);
        padG.gain.setValueAtTime(0.05, t + 0.5);
        padG.gain.linearRampToValueAtTime(0, t + 0.9);
        pad.connect(padG);
        toMaster(padG);
        pad.start(t);
        pad.stop(t + 1.0);
    }

    // ═══════════════════════════════════════════════════════════
    // 5. WARM WARNING — Smooth descending sine sweep
    //    Recognizable as a warning but NOT harsh or piercing
    // ═══════════════════════════════════════════════════════════
    function alertBuzz() {
        init();
        if (muted || !ctx) return;
        const t = ctx.currentTime;

        // Descending sine sweep: 200Hz → 100Hz (very smooth)
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.setValueAtTime(200, t);
        osc.frequency.exponentialRampToValueAtTime(100, t + 0.6);
        gain.gain.setValueAtTime(0, t);
        gain.gain.linearRampToValueAtTime(0.12, t + 0.08);
        gain.gain.setValueAtTime(0.12, t + 0.35);
        gain.gain.linearRampToValueAtTime(0, t + 0.7);
        osc.connect(gain);
        toMaster(gain);
        osc.start(t);
        osc.stop(t + 0.8);

        // Sub-bass rumble underneath
        const sub = ctx.createOscillator();
        const subG = ctx.createGain();
        sub.type = "sine";
        sub.frequency.value = 60;
        subG.gain.setValueAtTime(0, t);
        subG.gain.linearRampToValueAtTime(0.07, t + 0.05);
        subG.gain.linearRampToValueAtTime(0, t + 0.5);
        sub.connect(subG);
        toMaster(subG);
        sub.start(t);
        sub.stop(t + 0.6);
    }

    // ═══════════════════════════════════════════════════════════
    // 6. POWER UP — Warm analog sweep with gentle shimmer
    // ═══════════════════════════════════════════════════════════
    function powerUp() {
        init();
        if (muted || !ctx) return;
        const t = ctx.currentTime;

        // Rising sine sweep: 80Hz → 300Hz (warm range only)
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.setValueAtTime(80, t);
        osc.frequency.exponentialRampToValueAtTime(300, t + 1.2);
        gain.gain.setValueAtTime(0, t);
        gain.gain.linearRampToValueAtTime(0.10, t + 0.3);
        gain.gain.setValueAtTime(0.10, t + 0.9);
        gain.gain.linearRampToValueAtTime(0, t + 1.5);
        osc.connect(gain);
        toMaster(gain);
        osc.start(t);
        osc.stop(t + 1.6);

        // Detuned triangle for warmth
        const osc2 = ctx.createOscillator();
        const gain2 = ctx.createGain();
        osc2.type = "triangle";
        osc2.frequency.setValueAtTime(82, t);
        osc2.frequency.exponentialRampToValueAtTime(302, t + 1.2);
        gain2.gain.setValueAtTime(0, t);
        gain2.gain.linearRampToValueAtTime(0.04, t + 0.3);
        gain2.gain.linearRampToValueAtTime(0, t + 1.5);
        osc2.connect(gain2);
        toMaster(gain2);
        osc2.start(t);
        osc2.stop(t + 1.6);

        // Resolution chord at the end: C4 + E4
        warmOsc(261.63, "sine", t + 1.1, 0.6, 0.06);
        warmOsc(329.63, "sine", t + 1.15, 0.55, 0.04);
    }

    // ── Volume / Mute controls ────────────────────────────────
    function setVolume(v) {
        init();
        if (masterGain) masterGain.gain.value = Math.max(0, Math.min(1, v));
    }

    function toggleMute() {
        muted = !muted;
        if (masterGain) masterGain.gain.value = muted ? 0 : 0.35;
        return muted;
    }

    function isMuted() { return muted; }

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
