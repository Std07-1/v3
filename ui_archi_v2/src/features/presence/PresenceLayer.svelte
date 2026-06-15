<!--
    PresenceLayer — постійна присутність Арчі на рівні застосунку (Slice C).

    Рухоме WebGL-кільце + іскри-жар. Живе ЗАВЖДИ (фіксований шар), позиція керується
    маршрутом: ГОРН → центр, повна присутність; будь-який view → відступає вбік, зменшене.
    Перехід у view → спалах + рій іскор (його зусилля «тягне»). Dumb renderer (X28):
    стан приходить пропсами. Шейдер/resize — доведений патерн зі студії (innerWidth, on-event).

    SSOT досвіду: ui_archi_v2/PRESENCE_CONCEPT.md · самоопис: trader-v3/docs/archi_self_image.md

    Props:
      - mode: PresenceMode — поведінка (темп/яскравість/сон)
      - accent: string — колір настрою (--accent)
      - focused: boolean — відкрито view → кільце відступає вбік
      - wakeNonce: number — інкремент → спалах + іскри (новий перехід/імпульс)
-->
<script lang="ts">
    import { onMount, onDestroy } from "svelte";
    import type { PresenceMode } from "./presenceState";

    let {
        mode = "calm" as PresenceMode,
        accent = "#7c6fff",
        focused = false,
        wakeNonce = 0,
        idle = false,
        onArchiClick,
    } = $props<{
        mode?: PresenceMode;
        accent?: string;
        focused?: boolean;
        wakeNonce?: number;
        idle?: boolean;
        onArchiClick?: () => void;
    }>();

    type Behaviour = { rate: number; amp: number; pulse: number; alive: number };
    const BEHAVIOUR: Record<PresenceMode, Behaviour> = {
        calm:     { rate: 0.9, amp: 0.06, pulse: 0.82, alive: 1 },
        think:    { rate: 1.6, amp: 0.05, pulse: 0.95, alive: 1 },
        setup:    { rate: 0.7, amp: 0.07, pulse: 0.9,  alive: 1 },
        position: { rate: 1.5, amp: 0.045, pulse: 1.05, alive: 1 },
        alert:    { rate: 2.2, amp: 0.06, pulse: 1.15, alive: 1 },
        sleep:    { rate: 0.3, amp: 0.02, pulse: 0.42, alive: 0.16 },
    };
    const SLEEP_RGB = [0.29, 0.329, 0.439];
    const ALERT_RGB = [0.878, 0.392, 0.290];

    // позиції у фракціях вікна (стабільно за будь-якого розміру)
    const HOME = { fx: 0.5, fy: 0.5, r: 0.2 };
    function viewTarget() {
        return window.matchMedia("(max-width: 768px)").matches
            ? { fx: 0.5, fy: 0.12, r: 0.065 }   // мобілка: якір угорі над вікном
            : { fx: 0.09, fy: 0.5, r: 0.075 };  // desktop: відступає далі ліворуч (Стас: «віддали трошки»)
    }

    let ring: HTMLCanvasElement;
    let fxCanvas: HTMLCanvasElement;
    let hotspot: HTMLDivElement | undefined;
    let fxc: CanvasRenderingContext2D | null = null;
    let gl: WebGLRenderingContext | null = null;
    let raf = 0;
    let dpr = 1, cssW = 1, cssH = 1;
    let startMs = 0, lastMs = 0, wakeAt = -10;
    let lastWakeNonce = 0;
    let audioCtx: AudioContext | null = null;
    let wakeBurstTimers: ReturnType<typeof setTimeout>[] = [];
    let hovered = false;   // курсор над Архі (тягнешся забрати увагу)
    let hoverGlow = 0;     // згладжена органічна реакція кільця на наведення
    let idleDim = 1;       // idle-dim: кільце тьмяніє коли користувач довго без уваги
    let fxDirty = false;   // перф: чи треба чистити канвас іскор (тільки коли були іскри)
    let failed = $state(false);

    const cur = { fx: 0.5, fy: 0.5, r: 0.2, color: SLEEP_RGB.slice(), pulse: 0.42, alive: 0.16 };
    const sparks: Array<{ x: number; y: number; vx: number; vy: number; life: number; max: number; w: number }> = [];

    function hexToRgb(hex: string): number[] {
        const h = hex.trim().replace("#", "");
        if (h.length < 6) return [0.49, 0.44, 1.0];
        return [parseInt(h.slice(0, 2), 16) / 255, parseInt(h.slice(2, 4), 16) / 255, parseInt(h.slice(4, 6), 16) / 255];
    }
    function targetColor(): number[] {
        if (mode === "sleep") return SLEEP_RGB;
        if (mode === "alert") return ALERT_RGB;
        return hexToRgb(accent);
    }
    function tgt() {
        const t = focused ? viewTarget() : HOME;
        return t;
    }

    // ── WebGL (доведений шейдер зі студії, рухомий центр) ──
    const VERT = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
    const FRAG = `
precision highp float;
uniform vec2 u_res; uniform float u_time; uniform vec3 u_color;
uniform vec2 u_center; uniform float u_radius; uniform float u_pulse; uniform float u_wake;
float hash(vec2 p){ p=fract(p*vec2(123.34,456.21)); p+=dot(p,p+45.32); return fract(p.x*p.y); }
void main(){
  vec2 uv=(gl_FragCoord.xy-0.5*u_res)/min(u_res.x,u_res.y); uv-=u_center;
  float r=length(uv); float radius=u_radius+u_wake*0.03; float d=abs(r-radius);
  float thickness=0.0045+u_wake*0.004;
  float core=pow(thickness/(d+thickness),1.5);
  float glow=exp(-d*(26.0-u_wake*9.0));
  float halo=exp(-d*7.0)*0.4*smoothstep(radius*0.4,radius*0.9,r);
  float ringI=core*1.25+glow*0.95+halo; ringI+=u_wake*exp(-d*42.0)*2.2; ringI*=u_pulse;
  vec3 col=u_color*ringI; col+=vec3(1.0)*core*u_pulse*(0.30+u_wake*0.7);
  col=col/(1.0+col*0.55);
  float edgeFade=1.0-smoothstep(radius*1.7,radius*2.7,r);
  float a=clamp(max(col.r,max(col.g,col.b))*1.15,0.0,1.0)*edgeFade;
  float dth=(hash(gl_FragCoord.xy+fract(u_time))-0.5)/180.0; col+=dth; a=clamp(a+dth,0.0,1.0);
  gl_FragColor=vec4(col,a);
}`;
    const U: Record<string, WebGLUniformLocation | null> = {};

    function compile(type: number, src: string): WebGLShader {
        const s = gl!.createShader(type)!;
        gl!.shaderSource(s, src); gl!.compileShader(s);
        if (!gl!.getShaderParameter(s, gl!.COMPILE_STATUS)) throw new Error(gl!.getShaderInfoLog(s) || "shader");
        return s;
    }
    function initGL(): boolean {
        gl = ring.getContext("webgl", { antialias: true, alpha: true, premultipliedAlpha: false });
        if (!gl) return false;
        const prog = gl.createProgram()!;
        gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT));
        gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FRAG));
        gl.linkProgram(prog);
        if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog) || "link");
        gl.useProgram(prog);
        const buf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
        const loc = gl.getAttribLocation(prog, "p");
        gl.enableVertexAttribArray(loc);
        gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
        for (const n of ["u_res", "u_time", "u_color", "u_center", "u_radius", "u_pulse", "u_wake"]) {
            U[n] = gl.getUniformLocation(prog, n);
        }
        return true;
    }
    function resize(): void {
        if (!gl) return;
        // перф: кільце = м'який glow, високий dpr невидимий → кліпимо (моб 1.0, десктоп 1.5)
        const isMobile = window.matchMedia("(max-width: 768px)").matches;
        dpr = Math.min(window.devicePixelRatio || 1, isMobile ? 1 : 1.5);
        cssW = window.innerWidth; cssH = window.innerHeight;
        const bw = Math.round(cssW * dpr), bh = Math.round(cssH * dpr);
        ring.width = bw; ring.height = bh; gl.viewport(0, 0, bw, bh);
        fxCanvas.width = bw; fxCanvas.height = bh;
        if (fxc) fxc.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    // ── іскри-жар ──
    function ringScreen() { return { x: cur.fx * cssW, y: cur.fy * cssH, r: cur.r * Math.min(cssW, cssH) }; }
    function burst(n: number, power: number): void {
        const o = ringScreen(); const tx = cssW / 2, ty = cssH / 2;
        const base = Math.atan2(ty - o.y, tx - o.x);
        for (let i = 0; i < n; i++) {
            const ang = base + (Math.random() - 0.5) * 1.7, spd = (1.5 + Math.random() * 5) * power;
            sparks.push({ x: o.x + (Math.random() - 0.5) * o.r, y: o.y + (Math.random() - 0.5) * o.r,
                vx: Math.cos(ang) * spd, vy: Math.sin(ang) * spd - 0.6, life: 1, max: 0.45 + Math.random() * 0.6, w: 0.8 + Math.random() * 1.8 });
        }
    }
    function clearWakeBursts(): void {
        for (const t of wakeBurstTimers) clearTimeout(t);
        wakeBurstTimers = [];
    }
    // forge-«тяга»: рій іскор-жару + клац — зусилля, що тягне вікно з глибини.
    // Стаґер (3 хвилі) = багатший імпульс, ніж один сплеск (патерн зі study).
    function forgePull(): void {
        clearWakeBursts();
        burst(46, 1.0);
        lockClick();
        wakeBurstTimers.push(setTimeout(() => burst(28, 0.85), 90));
        wakeBurstTimers.push(setTimeout(() => burst(18, 0.7), 190));
    }
    function drawSparks(dt: number): void {
        if (!fxc) return;
        if (sparks.length === 0) {           // перф: нема іскор → не чистимо канвас щокадру
            if (fxDirty) { fxc.clearRect(0, 0, cssW, cssH); fxDirty = false; }
            return;
        }
        fxDirty = true;
        for (let i = sparks.length - 1; i >= 0; i--) {
            const s = sparks[i];
            s.x += s.vx; s.y += s.vy; s.vy += 0.05; s.vx *= 0.965; s.vy *= 0.965; s.life -= dt / s.max;
            if (s.life <= 0) sparks.splice(i, 1);
        }
        fxc.clearRect(0, 0, cssW, cssH);
        fxc.globalCompositeOperation = "lighter";
        for (const s of sparks) {
            const a = Math.max(0, s.life), spd = Math.hypot(s.vx, s.vy) || 1, len = Math.min(14, spd * 2.2);
            const nx = s.x - (s.vx / spd) * len, ny = s.y - (s.vy / spd) * len;
            const g = fxc.createLinearGradient(nx, ny, s.x, s.y);
            g.addColorStop(0, "rgba(255,150,60,0)");
            g.addColorStop(1, `rgba(255,${Math.round(190 + a * 60)},${Math.round(110 + a * 80)},${(a * 0.9).toFixed(3)})`);
            fxc.strokeStyle = g; fxc.lineWidth = s.w; fxc.lineCap = "round";
            fxc.beginPath(); fxc.moveTo(nx, ny); fxc.lineTo(s.x, s.y); fxc.stroke();
        }
        fxc.globalCompositeOperation = "source-over";
    }

    function ensureAudio(): AudioContext | null {
        try {
            if (!audioCtx) audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
            if (audioCtx.state === "suspended") audioCtx.resume();
            return audioCtx;
        } catch { return null; }
    }
    function lockClick(): void {
        const ac = ensureAudio(); if (!ac) return; const t = ac.currentTime;
        const b = (dl: number, f: number, q: number, gp: number, du: number) => {
            const n = Math.floor(ac.sampleRate * du), buf = ac.createBuffer(1, n, ac.sampleRate), ch = buf.getChannelData(0);
            for (let i = 0; i < n; i++) ch[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / n, 2.2);
            const src = ac.createBufferSource(); src.buffer = buf;
            const bp = ac.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = f; bp.Q.value = q;
            const g = ac.createGain();
            g.gain.setValueAtTime(0, t + dl); g.gain.linearRampToValueAtTime(gp, t + dl + 0.002);
            g.gain.exponentialRampToValueAtTime(0.0001, t + dl + du);
            src.connect(bp).connect(g).connect(ac.destination); src.start(t + dl); src.stop(t + dl + du);
        };
        b(0, 560, 7, 0.4, 0.05); b(0.05, 180, 4, 0.3, 0.1);
    }

    // wakeNonce змінився → спалах + іскри (новий перехід)
    $effect(() => {
        if (wakeNonce !== lastWakeNonce) {
            lastWakeNonce = wakeNonce;
            if (startMs > 0) {
                wakeAt = (performance.now() - startMs) / 1000;
                forgePull();
            }
        }
    });

    const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
    function frame(ms: number): void {
        if (!gl) return;
        if (startMs === 0) { startMs = ms; lastMs = ms; }
        const now = (ms - startMs) / 1000, dt = Math.min(0.05, (ms - lastMs) / 1000); lastMs = ms;
        const beh = BEHAVIOUR[mode] ?? BEHAVIOUR.calm, t = tgt();
        cur.fx = lerp(cur.fx, t.fx, 0.07); cur.fy = lerp(cur.fy, t.fy, 0.07); cur.r = lerp(cur.r, t.r, 0.07);
        const wake = Math.max(0, Math.exp(-(now - wakeAt) * 6.5));
        const tc = targetColor();
        for (let i = 0; i < 3; i++) cur.color[i] = lerp(cur.color[i], tc[i], 0.045);
        cur.alive = lerp(cur.alive, beh.alive, 0.045);
        let breath = Math.sin(now * beh.rate);
        if (mode === "think") breath = 0.55 * Math.sin(now * 1.6) + 0.3 * Math.sin(now * 2.73 + 1.3) + 0.15 * Math.sin(now * 4.11 + 2.1);
        cur.pulse = lerp(cur.pulse, beh.pulse * (1 + beh.amp * breath) + wake * 0.6, 0.12);
        // видиме дихання радіуса (живий навіть на home; мікро у сні через cur.alive)
        // + органічна реакція на наведення (плавне розгоряння+набухання, не плоский glow)
        hoverGlow = lerp(hoverGlow, hovered ? 1 : 0, 0.1);
        idleDim = lerp(idleDim, idle ? 0.5 : 1, 0.04); // плавне тьмяніння без уваги
        const rOut = cur.r * (1 + 0.03 * breath * cur.alive + hoverGlow * 0.05);
        const pulseOut = (cur.pulse + hoverGlow * 0.45) * idleDim;
        const m = Math.min(cssW, cssH);
        const ucx = (cur.fx * cssW - cssW / 2) / m, ucy = (cssH / 2 - cur.fy * cssH) / m;
        gl.uniform2f(U.u_res, ring.width, ring.height); gl.uniform1f(U.u_time, now);
        gl.uniform3f(U.u_color, cur.color[0], cur.color[1], cur.color[2]);
        gl.uniform2f(U.u_center, ucx, ucy); gl.uniform1f(U.u_radius, rOut);
        gl.uniform1f(U.u_pulse, pulseOut); gl.uniform1f(U.u_wake, wake);
        gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT); gl.drawArrays(gl.TRIANGLES, 0, 3);
        drawSparks(dt);
        // клік-зона приклеєна до кільця (його екранна позиція) — лише у фокусі
        if (focused && hotspot) {
            const o = ringScreen(), d = Math.max(72, o.r * 1.9);
            hotspot.style.width = hotspot.style.height = `${d}px`;
            hotspot.style.transform = `translate(${o.x - d / 2}px, ${o.y - d / 2}px)`;
        }
        raf = requestAnimationFrame(frame);
    }

    onMount(() => {
        try {
            if (!initGL()) { failed = true; return; }
            fxc = fxCanvas.getContext("2d");
        } catch (e) {
            console.warn("PRESENCE_LAYER_GL_FAIL:", e); failed = true; return;  // degraded-but-loud (I5)
        }
        resize();
        window.addEventListener("resize", resize);
        window.addEventListener("pointerdown", ensureAudio);
        raf = requestAnimationFrame(frame);
    });
    onDestroy(() => {
        cancelAnimationFrame(raf);
        clearWakeBursts();
        window.removeEventListener("resize", resize);
        window.removeEventListener("pointerdown", ensureAudio);
        audioCtx?.close().catch(() => {});
    });
</script>

<div class="presence-layer" class:focused>
    <canvas bind:this={ring} class="p-ring" class:hidden={failed}></canvas>
    <canvas bind:this={fxCanvas} class="p-fx"></canvas>
    {#if failed}
        <div class="p-fallback" style="--c:{mode === 'sleep' ? '#4a5470' : mode === 'alert' ? '#e0644a' : accent}"></div>
    {/if}
    <!-- клік-по-Арчі = забрати увагу назад (закрити вікно). Активна лише у фокусі. -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
        class="archi-hotspot"
        class:on={focused}
        bind:this={hotspot}
        onpointerdown={() => onArchiClick?.()}
        onpointerenter={() => (hovered = true)}
        onpointerleave={() => (hovered = false)}
        title="забрати увагу — назад у ГОРН"
    ></div>
</div>

<style>
    .presence-layer { position: fixed; inset: 0; z-index: 1; pointer-events: none; }
    .p-ring, .p-fx { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
    .p-ring.hidden { display: none; }
    /* Клік-зона приклеєна до кільця через JS-transform (frame loop) — нуль дублювання геометрії. */
    .archi-hotspot {
        position: absolute; top: 0; left: 0; width: 0; height: 0;
        border-radius: 50%; pointer-events: none; cursor: pointer;
        transition: box-shadow 0.2s ease;
    }
    .archi-hotspot.on { pointer-events: auto; }
    /* реакція на наведення — органічна, у самому кільці (WebGL hoverGlow), не CSS-glow */
    .p-fallback {
        position: absolute; top: 50%; left: 50%; width: 30%; aspect-ratio: 1;
        transform: translate(-50%, -50%); border-radius: 50%; border: 2px solid var(--c);
        box-shadow: 0 0 60px -10px var(--c);
    }
</style>
