// ***************************************************************
// SYNTHWAVE SFX ENGINE — Warm 80s synth sounds via Web Audio API
// ***************************************************************
//
// All sounds are synthesized in real-time. No MP3s needed.
// Warm analog-style tones with reverb and chorus for that
// smooth retrowave / outrun aesthetic.
//
// Usage:
//   RetroSFX.click()           — soft synth tap (buttons, tabs)
//   RetroSFX.computeStart()    — gentle pulsing pad (returns stop fn)
//   RetroSFX.modemHandshake()  — synthwave boot sequence (~3s)
//   RetroSFX.successChime()    — dreamy rising arpeggio
//   RetroSFX.alertBuzz()       — warm low warning tone
//   RetroSFX.powerUp()         — analog pad sweep on load
// ***************************************************************

const RetroSFX = (() => {
    let ctx = null;
    let masterGain = null;
    let reverbNode = null;
    let initialized = false;
    let muted = false;

    // ── Lazy init (requires user gesture) ─────────────────────
    const init = () => {
        if (initialized) return true;
        try {
            ctx = new (window.AudioContext || window.webkitAudioContext)();

            // Warm low-pass filter — removes harshness, keeps warmth
            const warmFilter = ctx.createBiquadFilter();
            warmFilter.type = "lowpass";
            warmFilter.frequency.value = 3500;
            warmFilter.Q.value = 0.5;

            // Gentle compressor
            const compressor = ctx.createDynamicsCompressor();
            compressor.threshold.value = -18;
            compressor.ratio.value = 3;
            compressor.attack.value = 0.01;
            compressor.release.value = 0.2;

            // Master volume — keep it gentle
            masterGain = ctx.createGain();
            masterGain.gain.value = 0.25;

            // Build reverb (convolution impulse)
            reverbNode = createReverb(1.2, 2.0);

            // Dry path: filter → compressor → master → out
            warmFilter.connect(compressor);
            compressor.connect(masterGain);
            masterGain.connect(ctx.destination);

            // Wet path: filter → reverb → master → out
            const reverbGain = ctx.createGain();
            reverbGain.gain.value = 0.3; // reverb mix
            warmFilter.connect(reverbGain);
            reverbGain.connect(reverbNode);
            reverbNode.connect(masterGain);

            // Store the entry point for all sounds
            ctx._masterInput = warmFilter;

            initialized = true;
            console.log("[RetroSFX] Initialized — Synthwave Audio Engine ready");
            return true;
        } catch (e) {
            console.warn("[RetroSFX] Web Audio API not available:", e);
            return false;
        }
    };

    // ── Helper: create simple reverb impulse ──────────────────
    const createReverb = (decay, duration) => {
        const rate = 44100;
        const len = rate * duration;
        const impulse = ctx.createBuffer(2, len, rate);
        for (let ch = 0; ch < 2; ch++) {
            const data = impulse.getChannelData(ch);
            for (let i = 0; i < len; i++) {
                data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, decay);
            }
        }
        const conv = ctx.createConvolver();
        conv.buffer = impulse;
        return conv;
    };

    // ── Helper: connect node → master chain ───────────────────
    const toMaster = (node) => {
        node.connect(ctx._masterInput);
        return node;
    };

    // ── Helper: detuned pair for chorus-like thickness ─────────
    const createDetunedPair = (freq, type, detuneCents = 8) => {
        const osc1 = ctx.createOscillator();
        const osc2 = ctx.createOscillator();
        osc1.type = type;
        osc2.type = type;
        osc1.frequency.value = freq;
        osc2.frequency.value = freq;
        osc1.detune.value = -detuneCents;
        osc2.detune.value = detuneCents;
        return [osc1, osc2];
    };

    // ═══════════════════════════════════════════════════════════
    // 1. SOFT SYNTH TAP — Gentle sine blip with harmonic
    // ═══════════════════════════════════════════════════════════
    const click = () => {
        if (!init() || muted) return;
        const t = ctx.currentTime;

        // Warm sine tap
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.setValueAtTime(880, t);
        osc.frequency.exponentialRampToValueAtTime(440, t + 0.06);
        gain.gain.setValueAtTime(0.15, t);
        gain.gain.exponentialRampToValueAtTime(0.001, t + 0.08);
        osc.connect(gain);
        toMaster(gain);
        osc.start(t);
        osc.stop(t + 0.1);

        // Soft harmonic overtone
        const h = ctx.createOscillator();
        const hg = ctx.createGain();
        h.type = "triangle";
        h.frequency.setValueAtTime(1320, t);
        h.frequency.exponentialRampToValueAtTime(660, t + 0.05);
        hg.gain.setValueAtTime(0.06, t);
        hg.gain.exponentialRampToValueAtTime(0.001, t + 0.06);
        h.connect(hg);
        toMaster(hg);
        h.start(t);
        h.stop(t + 0.08);
    };

    // ═══════════════════════════════════════════════════════════
    // 2. GENTLE PULSE PAD — Slow LFO-modulated sine pad
    //    Returns a stop() function to fade out and cancel
    // ═══════════════════════════════════════════════════════════
    const computeStart = () => {
        if (!init() || muted) return () => { };
        let running = true;
        const t = ctx.currentTime;

        // Base pad — detuned sine pair for warmth
        const [osc1, osc2] = createDetunedPair(220, "sine", 6);
        const padGain = ctx.createGain();
        padGain.gain.setValueAtTime(0, t);
        padGain.gain.linearRampToValueAtTime(0.06, t + 0.5);

        osc1.connect(padGain);
        osc2.connect(padGain);
        toMaster(padGain);

        // LFO for gentle pulse
        const lfo = ctx.createOscillator();
        const lfoGain = ctx.createGain();
        lfo.type = "sine";
        lfo.frequency.value = 2.5; // gentle pulse rate
        lfoGain.gain.value = 0.03;
        lfo.connect(lfoGain);
        lfoGain.connect(padGain.gain);

        osc1.start(t);
        osc2.start(t);
        lfo.start(t);

        // Occasional soft blip on top
        let blipTimer = null;
        const scheduleBlip = () => {
            if (!running) return;
            const now = ctx.currentTime;
            const note = [330, 440, 523.25, 659.25][Math.floor(Math.random() * 4)];
            const blipOsc = ctx.createOscillator();
            const blipGain = ctx.createGain();
            blipOsc.type = "triangle";
            blipOsc.frequency.value = note;
            blipGain.gain.setValueAtTime(0.04, now);
            blipGain.gain.exponentialRampToValueAtTime(0.001, now + 0.15);
            blipOsc.connect(blipGain);
            toMaster(blipGain);
            blipOsc.start(now);
            blipOsc.stop(now + 0.2);

            blipTimer = setTimeout(scheduleBlip, 300 + Math.random() * 500);
        };
        blipTimer = setTimeout(scheduleBlip, 200);

        // Return stop function with smooth fade-out
        return () => {
            running = false;
            if (blipTimer) clearTimeout(blipTimer);
            const now = ctx.currentTime;
            padGain.gain.cancelScheduledValues(now);
            padGain.gain.setValueAtTime(padGain.gain.value, now);
            padGain.gain.linearRampToValueAtTime(0, now + 0.5);
            osc1.stop(now + 0.6);
            osc2.stop(now + 0.6);
            lfo.stop(now + 0.6);
        };
    };

    // ═══════════════════════════════════════════════════════════
    // 3. SYNTHWAVE BOOT — Warm pad swell + arpeggio (~3s)
    //    Replaces the harsh modem handshake
    // ═══════════════════════════════════════════════════════════
    const modemHandshake = () => {
        if (!init() || muted) return;
        const t = ctx.currentTime;

        // Phase 1: Sub bass swell (0 → 1.5s)
        const sub = ctx.createOscillator();
        const subGain = ctx.createGain();
        sub.type = "sine";
        sub.frequency.setValueAtTime(55, t);
        sub.frequency.linearRampToValueAtTime(110, t + 1.5);
        subGain.gain.setValueAtTime(0, t);
        subGain.gain.linearRampToValueAtTime(0.12, t + 0.8);
        subGain.gain.linearRampToValueAtTime(0.08, t + 1.5);
        subGain.gain.linearRampToValueAtTime(0, t + 2.0);
        sub.connect(subGain);
        toMaster(subGain);
        sub.start(t);
        sub.stop(t + 2.1);

        // Phase 2: Warm pad chord (0.5 → 2.5s) — Cmaj7 voicing
        const padNotes = [261.63, 329.63, 392, 493.88]; // C4, E4, G4, B4
        padNotes.forEach((freq) => {
            const [o1, o2] = createDetunedPair(freq, "triangle", 10);
            const g = ctx.createGain();
            g.gain.setValueAtTime(0, t + 0.5);
            g.gain.linearRampToValueAtTime(0.04, t + 1.2);
            g.gain.linearRampToValueAtTime(0.03, t + 2.0);
            g.gain.exponentialRampToValueAtTime(0.001, t + 2.8);
            o1.connect(g);
            o2.connect(g);
            toMaster(g);
            o1.start(t + 0.5);
            o2.start(t + 0.5);
            o1.stop(t + 3.0);
            o2.stop(t + 3.0);
        });

        // Phase 3: Descending arpeggio sparkle (1.5 → 3.0s)
        const arpNotes = [987.77, 783.99, 659.25, 523.25, 392]; // B5→G4
        arpNotes.forEach((freq, i) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = "sine";
            osc.frequency.value = freq;
            const start = t + 1.5 + i * 0.2;
            gain.gain.setValueAtTime(0.08, start);
            gain.gain.exponentialRampToValueAtTime(0.001, start + 0.4);
            osc.connect(gain);
            toMaster(gain);
            osc.start(start);
            osc.stop(start + 0.5);
        });
    };

    // ═══════════════════════════════════════════════════════════
    // 4. SUCCESS CHIME — Dreamy pentatonic arpeggio with shimmer
    // ═══════════════════════════════════════════════════════════
    const successChime = () => {
        if (!init() || muted) return;
        const t = ctx.currentTime;
        // Pentatonic rise: C5 → D5 → E5 → G5 → shimmer on C6
        const notes = [523.25, 587.33, 659.25, 783.99, 1046.50];

        notes.forEach((freq, i) => {
            const [o1, o2] = createDetunedPair(freq, "sine", 6);
            const gain = ctx.createGain();
            const start = t + i * 0.1;
            const isLast = i === notes.length - 1;
            const dur = isLast ? 0.6 : 0.25;

            gain.gain.setValueAtTime(isLast ? 0.12 : 0.1, start);
            gain.gain.exponentialRampToValueAtTime(0.001, start + dur);

            o1.connect(gain);
            o2.connect(gain);
            toMaster(gain);
            o1.start(start);
            o2.start(start);
            o1.stop(start + dur + 0.05);
            o2.stop(start + dur + 0.05);
        });

        // Shimmer: high sine harmonic on the last note
        const shimmer = ctx.createOscillator();
        const sGain = ctx.createGain();
        shimmer.type = "sine";
        shimmer.frequency.value = 2093; // C7
        const sStart = t + 0.4;
        sGain.gain.setValueAtTime(0.03, sStart);
        sGain.gain.exponentialRampToValueAtTime(0.001, sStart + 0.8);
        shimmer.connect(sGain);
        toMaster(sGain);
        shimmer.start(sStart);
        shimmer.stop(sStart + 0.9);
    };

    // ═══════════════════════════════════════════════════════════
    // 5. WARM ALERT — Gentle low tone (not harsh buzz)
    // ═══════════════════════════════════════════════════════════
    const alertBuzz = () => {
        if (!init() || muted) return;
        const t = ctx.currentTime;

        // Two falling sine tones — warm, not aggressive
        const osc1 = ctx.createOscillator();
        const osc2 = ctx.createOscillator();
        const gain = ctx.createGain();
        osc1.type = "sine";
        osc2.type = "triangle";
        osc1.frequency.setValueAtTime(440, t);
        osc1.frequency.exponentialRampToValueAtTime(220, t + 0.2);
        osc2.frequency.setValueAtTime(330, t);
        osc2.frequency.exponentialRampToValueAtTime(165, t + 0.25);

        gain.gain.setValueAtTime(0.15, t);
        gain.gain.setValueAtTime(0.12, t + 0.15);
        gain.gain.exponentialRampToValueAtTime(0.001, t + 0.35);

        osc1.connect(gain);
        osc2.connect(gain);
        toMaster(gain);
        osc1.start(t);
        osc2.start(t);
        osc1.stop(t + 0.4);
        osc2.stop(t + 0.4);
    };

    // ═══════════════════════════════════════════════════════════
    // 6. POWER UP — Analog synth pad sweep with shimmer
    // ═══════════════════════════════════════════════════════════
    const powerUp = () => {
        if (!init() || muted) return;
        const t = ctx.currentTime;

        // Rising pad swell
        const [o1, o2] = createDetunedPair(110, "triangle", 12);
        const padGain = ctx.createGain();
        o1.frequency.exponentialRampToValueAtTime(440, t + 0.8);
        o2.frequency.exponentialRampToValueAtTime(440, t + 0.8);
        padGain.gain.setValueAtTime(0, t);
        padGain.gain.linearRampToValueAtTime(0.1, t + 0.3);
        padGain.gain.setValueAtTime(0.1, t + 0.6);
        padGain.gain.exponentialRampToValueAtTime(0.001, t + 1.2);
        o1.connect(padGain);
        o2.connect(padGain);
        toMaster(padGain);
        o1.start(t);
        o2.start(t);
        o1.stop(t + 1.3);
        o2.stop(t + 1.3);

        // Arrival chime — two gentle notes
        const chimeNotes = [659.25, 880]; // E5, A5
        chimeNotes.forEach((freq, i) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = "sine";
            osc.frequency.value = freq;
            const start = t + 0.7 + i * 0.12;
            gain.gain.setValueAtTime(0.1, start);
            gain.gain.exponentialRampToValueAtTime(0.001, start + 0.4);
            osc.connect(gain);
            toMaster(gain);
            osc.start(start);
            osc.stop(start + 0.5);
        });
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
