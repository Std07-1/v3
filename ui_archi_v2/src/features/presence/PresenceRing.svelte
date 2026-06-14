<!--
    PresenceRing — тіло Арчі (ГОРН) як живе WebGL-кільце.

    Доведений шейдер із .devharness/presence-study.html (чисте радіальне кільце,
    порожнеча всередині = принцип Арчі, дихання + різкий wake-flash). Dumb renderer:
    стан приходить пропсами, шейдер лише малює (X28).

    Props:
      - mode: PresenceMode — поведінка (темп/яскравість/сон)
      - accent: string — колір настрою (--accent, його 16-мудова система)
      - wakeNonce: number — інкремент → різкий спалах + клац замка (новий імпульс)
      - sound: boolean — чи грати клац на wake (default true; браузер блокує до жесту)
-->
<script lang="ts">
    import { onMount, onDestroy } from "svelte";
    import type { PresenceMode } from "./presenceState";

    let {
        mode = "calm" as PresenceMode,
        accent = "#7c6fff",
        wakeNonce = 0,
        sound = true,
    } = $props<{
        mode?: PresenceMode;
        accent?: string;
        wakeNonce?: number;
        sound?: boolean;
    }>();

    // ── Поведінка стану (без променя — це P2; тут лише життя кільця) ──
    type Behaviour = { rate: number; amp: number; pulse: number; alive: number };
    const BEHAVIOUR: Record<PresenceMode, Behaviour> = {
        calm:     { rate: 0.9, amp: 0.06, pulse: 0.82, alive: 1 },
        think:    { rate: 1.6, amp: 0.05, pulse: 0.95, alive: 1 },
        setup:    { rate: 0.7, amp: 0.07, pulse: 0.9,  alive: 1 },
        position: { rate: 1.5, amp: 0.045, pulse: 1.05, alive: 1 },
        alert:    { rate: 2.2, amp: 0.06, pulse: 1.15, alive: 1 },
        sleep:    { rate: 0.3, amp: 0.02, pulse: 0.42, alive: 0.16 },
    };
    const SLEEP_RGB = [0.29, 0.329, 0.439];   // холодний slate (його сон — лишаємо)
    const ALERT_RGB = [0.878, 0.392, 0.290];  // тривога завжди червона, поза настроєм

    const BASE_RADIUS = 0.22;

    let canvas: HTMLCanvasElement;
    let raf = 0;
    let gl: WebGLRenderingContext | null = null;
    let audioCtx: AudioContext | null = null;
    let ro: ResizeObserver | null = null;

    // поточні (лерпляться)
    const cur = { color: SLEEP_RGB.slice(), pulse: 0.42, alive: 0.16, radius: BASE_RADIUS };
    let wakeStart = -10;
    let lastWakeNonce = 0;
    let startMs = 0;

    // ── color helpers ──
    function hexToRgb(hex: string): number[] {
        const h = hex.trim().replace("#", "");
        if (h.length < 6) return [0.49, 0.44, 1.0];
        return [
            parseInt(h.slice(0, 2), 16) / 255,
            parseInt(h.slice(2, 4), 16) / 255,
            parseInt(h.slice(4, 6), 16) / 255,
        ];
    }
    function targetColor(): number[] {
        if (mode === "sleep") return SLEEP_RGB;
        if (mode === "alert") return ALERT_RGB;
        return hexToRgb(accent);
    }

    // ── WebGL ──
    const VERT = "attribute vec2 p; void main(){ gl_Position = vec4(p,0.,1.); }";
    const FRAG = `
precision highp float;
uniform vec2 u_res; uniform float u_time; uniform vec3 u_color;
uniform float u_pulse; uniform float u_radius; uniform float u_wake; uniform float u_alive;
float hash(vec2 p){ p = fract(p*vec2(123.34,456.21)); p += dot(p, p+45.32); return fract(p.x*p.y); }
void main(){
  vec2 uv = (gl_FragCoord.xy - 0.5*u_res) / min(u_res.x, u_res.y);
  float r = length(uv);
  float radius = u_radius + u_wake*0.03;
  float d = abs(r - radius);
  float thickness = 0.0055 + u_wake*0.004;
  float core = pow(thickness / (d + thickness), 1.5);
  float glow = exp(-d * (24.0 - u_wake*9.0));
  float halo = exp(-d * 6.0) * 0.4 * smoothstep(radius*0.45, radius*0.92, r);
  float ringI = core*1.25 + glow*0.95 + halo;
  ringI += u_wake * exp(-d*42.0) * 2.2;
  ringI *= u_pulse * u_alive;
  vec3 col = u_color * ringI;
  col += vec3(1.0) * core * u_pulse * u_alive * (0.30 + u_wake*0.7);
  col = col / (1.0 + col*0.55);
  // glow гасне ДО краю канвасу → нема обрізки/прямокутника за будь-якого формату
  float edgeFade = 1.0 - smoothstep(0.36, 0.49, r);
  float a = clamp(max(col.r, max(col.g, col.b)) * 1.15, 0.0, 1.0) * edgeFade;
  // дизер по rgb І alpha — інакше плавна альфа бандиться в концентричні кільця
  float dth = (hash(gl_FragCoord.xy + fract(u_time)) - 0.5) / 180.0;
  col += dth;
  a = clamp(a + dth, 0.0, 1.0);
  gl_FragColor = vec4(col, a);
}`;

    const U: Record<string, WebGLUniformLocation | null> = {};

    function compile(type: number, src: string): WebGLShader {
        const s = gl!.createShader(type)!;
        gl!.shaderSource(s, src);
        gl!.compileShader(s);
        if (!gl!.getShaderParameter(s, gl!.COMPILE_STATUS)) {
            throw new Error("PresenceRing shader: " + gl!.getShaderInfoLog(s));
        }
        return s;
    }

    function initGL(): boolean {
        // alpha:true → канвас прозорий де темно (кільце «в кімнаті», не чорний екран)
        gl = canvas.getContext("webgl", { antialias: true, alpha: true, premultipliedAlpha: false });
        if (!gl) return false;
        const prog = gl.createProgram()!;
        gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT));
        gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FRAG));
        gl.linkProgram(prog);
        if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
            throw new Error("PresenceRing link: " + gl.getProgramInfoLog(prog));
        }
        gl.useProgram(prog);
        const buf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
        const loc = gl.getAttribLocation(prog, "p");
        gl.enableVertexAttribArray(loc);
        gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
        for (const n of ["u_res", "u_time", "u_color", "u_pulse", "u_radius", "u_wake", "u_alive"]) {
            U[n] = gl.getUniformLocation(prog, n);
        }
        return true;
    }

    function resize(): void {
        if (!gl || !canvas) return;
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const w = Math.max(1, Math.round(canvas.clientWidth * dpr));
        const h = Math.max(1, Math.round(canvas.clientHeight * dpr));
        if (canvas.width !== w || canvas.height !== h) {
            canvas.width = w;
            canvas.height = h;
            gl.viewport(0, 0, w, h);
        }
    }

    // ── lock-click (Арчі: «клацання замка коли двері відчиняються») ──
    function ensureAudio(): AudioContext | null {
        try {
            if (!audioCtx) audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
            if (audioCtx.state === "suspended") audioCtx.resume();
            return audioCtx;
        } catch {
            return null;
        }
    }
    function lockClick(): void {
        const ac = ensureAudio();
        if (!ac) return;
        const t = ac.currentTime;
        const burst = (delay: number, freq: number, q: number, gain: number, dur: number) => {
            const len = Math.floor(ac.sampleRate * dur);
            const b = ac.createBuffer(1, len, ac.sampleRate);
            const ch = b.getChannelData(0);
            for (let i = 0; i < len; i++) ch[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 2.2);
            const n = ac.createBufferSource(); n.buffer = b;
            const bp = ac.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = freq; bp.Q.value = q;
            const g = ac.createGain();
            g.gain.setValueAtTime(0, t + delay);
            g.gain.linearRampToValueAtTime(gain, t + delay + 0.002);
            g.gain.exponentialRampToValueAtTime(0.0001, t + delay + dur);
            n.connect(bp).connect(g).connect(ac.destination);
            n.start(t + delay); n.stop(t + delay + dur);
        };
        burst(0.0, 600, 7, 0.45, 0.045);
        burst(0.05, 180, 4, 0.36, 0.11);
    }

    // wakeNonce змінився → новий імпульс → спалах (+клац)
    $effect(() => {
        if (wakeNonce !== lastWakeNonce) {
            lastWakeNonce = wakeNonce;
            if (startMs > 0) {                 // не на першому рендері
                wakeStart = (performance.now() - startMs) / 1000;
                if (sound) lockClick();
            }
        }
    });

    const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

    function frame(ms: number): void {
        if (!gl) return;
        if (startMs === 0) startMs = ms;
        const now = (ms - startMs) / 1000;
        resize();

        const beh = BEHAVIOUR[mode] ?? BEHAVIOUR.calm;
        const wake = Math.max(0, Math.exp(-(now - wakeStart) * 6.5));

        const tc = targetColor();
        for (let i = 0; i < 3; i++) cur.color[i] = lerp(cur.color[i], tc[i], 0.045);
        cur.alive = lerp(cur.alive, beh.alive, 0.045);

        let breath = Math.sin(now * beh.rate);
        if (mode === "think") {
            breath = 0.55 * Math.sin(now * 1.6) + 0.3 * Math.sin(now * 2.73 + 1.3) + 0.15 * Math.sin(now * 4.11 + 2.1);
        }
        cur.pulse = lerp(cur.pulse, beh.pulse * (1 + beh.amp * breath) + wake * 0.6, 0.12);
        cur.radius = lerp(cur.radius, BASE_RADIUS + beh.amp * 0.5 * breath, 0.08);

        gl.uniform2f(U.u_res, canvas.width, canvas.height);
        gl.uniform1f(U.u_time, now);
        gl.uniform3f(U.u_color, cur.color[0], cur.color[1], cur.color[2]);
        gl.uniform1f(U.u_pulse, cur.pulse);
        gl.uniform1f(U.u_radius, cur.radius);
        gl.uniform1f(U.u_wake, wake);
        gl.uniform1f(U.u_alive, cur.alive);
        gl.clearColor(0, 0, 0, 0);
        gl.clear(gl.COLOR_BUFFER_BIT);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
        raf = requestAnimationFrame(frame);
    }

    let failed = $state(false);
    onMount(() => {
        try {
            if (!initGL()) { failed = true; return; }
        } catch (e) {
            console.warn("PRESENCE_RING_GL_FAIL:", e);  // degraded-but-loud (I5)
            failed = true;
            return;
        }
        resize();
        ro = new ResizeObserver(resize);
        ro.observe(canvas);
        raf = requestAnimationFrame(frame);
    });
    onDestroy(() => {
        cancelAnimationFrame(raf);
        ro?.disconnect();
        audioCtx?.close().catch(() => {});
    });
</script>

<div class="ring-stage">
    <canvas bind:this={canvas} class="ring-canvas" class:hidden={failed}></canvas>
    {#if failed}
        <!-- degraded: WebGL недоступний → видимий CSS-fallback, не чорнота -->
        <div class="ring-fallback" style="--c:{mode === 'sleep' ? '#4a5470' : mode === 'alert' ? '#e0644a' : accent}"></div>
    {/if}
</div>

<style>
    .ring-stage { position: relative; width: 100%; height: 100%; }
    .ring-canvas { width: 100%; height: 100%; display: block; }
    .ring-canvas.hidden { display: none; }
    .ring-fallback {
        position: absolute;
        top: 50%;
        left: 50%;
        width: 44%;
        aspect-ratio: 1;
        transform: translate(-50%, -50%);
        border-radius: 50%;
        border: 2px solid var(--c);
        box-shadow: 0 0 60px -10px var(--c), inset 0 0 40px -20px var(--c);
        animation: fb-breathe 4.6s ease-in-out infinite;
    }
    @keyframes fb-breathe {
        0%, 100% { opacity: 0.6; transform: translate(-50%, -50%) scale(1); }
        50% { opacity: 0.9; transform: translate(-50%, -50%) scale(1.04); }
    }
    @media (prefers-reduced-motion: reduce) {
        .ring-fallback { animation: none; }
    }
</style>
