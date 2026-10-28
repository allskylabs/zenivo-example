// 3D dome (lazy chunk, three.js): stars, Milky Way, ground and atmosphere haze (celestial sphere only, no Earth globe). Lines, labels, planets,
// meteors and markers come from the shared overlay (layers.ts) drawn with this camera's projection.
// Scene frame: +X East, +Y Zenith, -Z North.
import {
  WebGLRenderer, Scene, PerspectiveCamera, Group, Points, BufferGeometry, Float32BufferAttribute, ShaderMaterial, Mesh, SphereGeometry,
  CircleGeometry, MeshBasicMaterial, MeshLambertMaterial, LineBasicMaterial, LineSegments, LineLoop, Matrix3, Matrix4, Vector3,
  DataTexture, RedFormat, UnsignedByteType, LinearFilter, RepeatWrapping, ClampToEdgeWrapping, BackSide, AdditiveBlending, DirectionalLight, AmbientLight, DoubleSide,
} from 'three';
import { bvToRgb, mat3MulVec, radecToVec } from '@astrophoto/sky-engine';
import { parseStarTile } from '@astrophoto/sky-data';
import type { SkyCore } from './engine-adapter';
import { drawLayers } from './layers';
import type { Proj } from './layers';
import { DEG } from './astro';

const RD = 80, GR = 14;
const GAL = [-0.0548755604162154, -0.873437090234885, -0.4838350155487132, 0.4941094278755837, -0.4448296299600112, 0.7469822444972189, -0.8676661490190047, -0.1980763734312015, 0.4559837761750669];

export interface DomeHandle { enter(): void; exit(): void }

export async function createDome(core: SkyCore, root: HTMLElement): Promise<DomeHandle> {
  const canvas = document.createElement('canvas'); canvas.className = 'sv-dome'; canvas.style.display = 'none';
  root.insertBefore(canvas, core.ov);
  const renderer = new WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(window.devicePixelRatio || 1); renderer.setClearColor(0x02030a, 1);
  const scene = new Scene();
  const camera = new PerspectiveCamera(50, 1, 0.05, 2000);
  const eq = new Group(); eq.matrixAutoUpdate = false; scene.add(eq);

  // stars (order-0 tiles, mag <= 6.5)
  const starMat = new ShaderMaterial({
    transparent: true, depthWrite: false, blending: AdditiveBlending, uniforms: { uPx: { value: window.devicePixelRatio || 1 } },
    vertexShader: 'attribute float size; attribute vec3 color; varying vec3 vC; uniform float uPx; void main(){vC=color; gl_PointSize=size*uPx; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',
    fragmentShader: 'varying vec3 vC; void main(){float a=smoothstep(.5,.0,length(gl_PointCoord-.5)); gl_FragColor=vec4(vC*a,a);}',
  });
  const stars = new Points(new BufferGeometry(), starMat); stars.frustumCulled = false; eq.add(stars);
  void (async () => {
    try {
      const idx = await (await fetch(core.cfg.dataBaseUrl + 'stars/index.json')).json() as { tiles: Record<string, Record<string, number>> };
      const pos: number[] = [], col: number[] = [], size: number[] = [];
      await Promise.all(Object.keys(idx.tiles['0'] ?? {}).map(async pix => {
        const r = await fetch(`${core.cfg.dataBaseUrl}stars/o0/${pix}.stl`); if (!r.ok) return;
        const t = parseStarTile(await r.arrayBuffer());
        for (let i = 0; i < t.count; i++) {
          const m = t.mag[i]!; pos.push(t.vec[3 * i]! * RD * 0.99, t.vec[3 * i + 1]! * RD * 0.99, t.vec[3 * i + 2]! * RD * 0.99);
          const [r2, g, b] = bvToRgb(t.bv[i]!); const br = Math.min(1, Math.max(0.25, 1.15 - 0.13 * m));
          col.push(r2 * br, g * br, b * br); size.push(Math.min(5.5, Math.max(1.8, 4.6 - 0.62 * m)));
        }
      }));
      const g = new BufferGeometry();
      g.setAttribute('position', new Float32BufferAttribute(pos, 3)); g.setAttribute('color', new Float32BufferAttribute(col, 3)); g.setAttribute('size', new Float32BufferAttribute(size, 1));
      stars.geometry = g;
    } catch (e) { console.error('dome stars', e); }
  })();

  // Milky Way sphere (galactic texture sampled by direction)
  const milkyMat = new ShaderMaterial({
    transparent: true, depthWrite: false, blending: AdditiveBlending, side: BackSide,
    uniforms: { tex: { value: null }, uG: { value: new Matrix3().set(...GAL as [number, number, number, number, number, number, number, number, number]) }, k: { value: 0.55 } },
    vertexShader: 'varying vec3 vP; void main(){vP=position; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',
    fragmentShader: 'uniform sampler2D tex; uniform mat3 uG; uniform float k; varying vec3 vP; void main(){vec3 g=uG*normalize(vP); float l=atan(g.y,g.x); if(l<0.) l+=6.2831853; float b=asin(clamp(g.z,-1.,1.)); float v=texture2D(tex,vec2(l/6.2831853,.5-b/3.14159265)).r; gl_FragColor=vec4(vec3(.55,.65,.95)*v*k,1.);}',
  });
  const milky = new Mesh(new SphereGeometry(RD * 1.01, 128, 64), milkyMat); milky.frustumCulled = false; eq.add(milky);
  let milkyDone = false;
  const ensureMilky = (): void => {
    if (milkyDone || !core.milkyImg) return; milkyDone = true;
    const im = core.milkyImg; const t = new DataTexture(im.data, im.width, im.height, RedFormat, UnsignedByteType);
    t.minFilter = t.magFilter = LinearFilter; t.wrapS = RepeatWrapping; t.wrapT = ClampToEdgeWrapping; t.needsUpdate = true; milkyMat.uniforms.tex!.value = t;
  };

  // atmosphere haze
  const hazeMat = new ShaderMaterial({
    transparent: true, depthWrite: false, side: BackSide, uniforms: { sun: { value: -1 } },
    vertexShader: 'varying vec3 vP; void main(){vP=position; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',
    fragmentShader: 'uniform float sun; varying vec3 vP; void main(){float h=normalize(vP).y; float day=smoothstep(-0.22,0.1,sun); float tw=smoothstep(-0.32,-0.05,sun)*(1.-smoothstep(-0.05,0.12,sun)); vec3 zen=mix(vec3(.02,.03,.1),vec3(.25,.5,.85),day); vec3 hor=mix(vec3(.05,.07,.15),vec3(.7,.82,.95),day); hor=mix(hor,vec3(.95,.5,.25),tw); float band=pow(1.-clamp(abs(h),0.,1.),3.); vec3 c=mix(zen,hor,band); float a=clamp(day*.9+tw*.4+band*(.18+.25*day),0.,.95); gl_FragColor=vec4(c,a);}',
  });
  const haze = new Mesh(new SphereGeometry(RD * 1.02, 128, 64), hazeMat); haze.renderOrder = 2; scene.add(haze);

  // ground + horizon
  const ground = new Mesh(new CircleGeometry(RD, 256), new MeshBasicMaterial({ color: 0x0c1511, transparent: true, opacity: 0.95, side: DoubleSide, depthWrite: false }));
  ground.rotation.x = -Math.PI / 2; ground.position.y = -0.02; ground.renderOrder = 3; scene.add(ground);
  const ringPts: number[] = []; for (let i = 0; i < 256; i++) { const a = i / 256 * 2 * Math.PI; ringPts.push(RD * Math.sin(a), 0, -RD * Math.cos(a)); }
  const ring = new LineLoop(new BufferGeometry().setAttribute('position', new Float32BufferAttribute(ringPts, 3)), new LineBasicMaterial({ color: 0x34d399 })); scene.add(ring);
  const gpts: number[] = []; for (const r of [20, 40, 60]) for (let i = 0; i < 64; i++) { const a = i / 64 * 2 * Math.PI, b = (i + 1) / 64 * 2 * Math.PI; gpts.push(r * Math.sin(a), 0, -r * Math.cos(a), r * Math.sin(b), 0, -r * Math.cos(b)); }
  const grid = new LineSegments(new BufferGeometry().setAttribute('position', new Float32BufferAttribute(gpts, 3)), new LineBasicMaterial({ color: 0x1f3a30, transparent: true, opacity: 0.5 })); scene.add(grid);

  const sunLight = new DirectionalLight(0xffffff, 2.4); scene.add(sunLight, new AmbientLight(0x6070a0, 0.7));

  // camera control
  const st = { yaw: 0, pitch: 0.38, dist: 170, inside: false, fov: 50 };
  const camPos = new Vector3(), tgt = new Vector3(), tmp = new Vector3();
  const applyCamera = (): void => {
    const cp = Math.cos(st.pitch), sp = Math.sin(st.pitch);
    const dir = new Vector3(Math.sin(st.yaw) * cp, sp, Math.cos(st.yaw) * cp);
    if (st.inside) { camPos.set(0, 0.4, 0); tgt.copy(camPos).addScaledVector(dir, -1); camera.fov = st.fov; }
    else { camPos.copy(dir).multiplyScalar(st.dist); tgt.set(0, 0, 0); camera.fov = 50; }
    camera.position.copy(camPos); camera.lookAt(tgt); camera.updateProjectionMatrix(); camera.updateMatrixWorld();
  };
  const rel = (e: { clientX: number; clientY: number }): [number, number] => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  const ptrs = new Map<number, [number, number]>(); let pinch = 0, moved = 0, downAt = 0, down: [number, number] = [0, 0];
  const onDown = (e: PointerEvent): void => { if (core.mode !== '3d' || (e.target as HTMLElement).closest('.sv-ui')) return; root.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, rel(e)); if (ptrs.size === 1) { moved = 0; downAt = performance.now(); down = rel(e); } else { moved = 99; const [a, b] = [...ptrs.values()]; pinch = Math.hypot(a![0] - b![0], a![1] - b![1]); } };
  const zoom = (f: number): void => { if (st.inside) st.fov = Math.max(20, Math.min(100, st.fov / f)); else st.dist = Math.max(70, Math.min(420, st.dist / f)); };
  const onMove = (e: PointerEvent): void => {
    const p = ptrs.get(e.pointerId); if (!p) return; const c = rel(e);
    if (ptrs.size === 1) { moved = Math.max(moved, Math.hypot(c[0] - down[0], c[1] - down[1])); const k = st.inside ? -1 : 1; st.yaw -= k * (c[0] - p[0]) * 0.005 * (st.inside ? camera.fov / 75 : 1); st.pitch = Math.max(-1.5, Math.min(1.5, st.pitch + k * (c[1] - p[1]) * 0.005 * (st.inside ? camera.fov / 75 : 1))); }
    else if (ptrs.size === 2) { ptrs.set(e.pointerId, c); const [a, b] = [...ptrs.values()]; const d = Math.hypot(a![0] - b![0], a![1] - b![1]); if (pinch > 0) zoom(d / pinch); pinch = d; }
    ptrs.set(e.pointerId, c);
  };
  const onUp = (e: PointerEvent): void => { const was = ptrs.size; ptrs.delete(e.pointerId); pinch = 0; if (e.type === 'pointerup' && was === 1 && moved < 6 && performance.now() - downAt < 600 && core.mode === '3d') core.select(core.pick(rel(e))); };
  root.addEventListener('pointerdown', onDown); root.addEventListener('pointermove', onMove); root.addEventListener('pointerup', onUp); root.addEventListener('pointercancel', onUp);
  root.addEventListener('wheel', e => { if (core.mode !== '3d' || (e.target as HTMLElement).closest('.sv-ui')) return; e.preventDefault(); zoom(Math.exp(-e.deltaY * 0.0015)); }, { passive: false });

  const v3 = new Vector3();
  const sceneOf = (d: [number, number, number] | number[]): any => v3.set(-d[1]! * RD, d[2]! * RD, -d[0]! * RD);
  const proj = (): Proj => {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    return {
      w, h, fovDeg: camera.fov, clipBelow: !core.layers.seeThroughGround, noBackdrop: true,
      pxPerRad: (h / 2) / Math.tan(camera.fov * DEG / 2) * (st.inside ? 1 : 0.4),
      project: d => {
        const s = sceneOf(d); tmp.copy(s).applyMatrix4(camera.matrixWorldInverse); if (tmp.z >= -0.01) return null;
        s.project(camera); return { x: (s.x * 0.5 + 0.5) * w, y: (-s.y * 0.5 + 0.5) * h };
      },
    };
  };

  const tick = (_dt: number, _force: boolean): void => {
    ensureMilky();
    const w = Math.max(1, root.clientWidth), h = Math.max(1, root.clientHeight);
    if (canvas.width !== Math.round(w * renderer.getPixelRatio()) || canvas.height !== Math.round(h * renderer.getPixelRatio())) { renderer.setSize(w, h, false); camera.aspect = w / h; }
    applyCamera();
    const M = core.snap.M;
    // equatorial (J2000) -> scene: scene = S * M with S = [[0,-1,0],[0,0,1],[-1,0,0]]
    eq.matrix.set(-M[3], -M[4], -M[5], 0, M[6], M[7], M[8], 0, -M[0], -M[1], -M[2], 0, 0, 0, 0, 1); eq.matrixWorldNeedsUpdate = true;
    const sun = core.snap.bodies[0];
    if (sun) {
      const sd = mat3MulVec(M, radecToVec({ raRad: sun.raDeg * DEG, decRad: sun.decDeg * DEG }));
      hazeMat.uniforms.sun!.value = sd[2]; sunLight.position.set(-sd[1] * 100, sd[2] * 100, -sd[0] * 100);
    }
    const L = core.layers;
    stars.visible = L.stars; milky.visible = L.milkyWay && milkyDone; haze.visible = L.daylightAtmosphere;
    grid.visible = true;
    (ground.material as any).opacity = L.seeThroughGround ? 0.1 : 0.6;
    renderer.render(scene, camera);
    // overlay
    const ctx = core.ov.getContext('2d')!; const dpr = window.devicePixelRatio || 1;
    if (core.ov.width !== Math.round(w * dpr) || core.ov.height !== Math.round(h * dpr)) { core.ov.width = Math.round(w * dpr); core.ov.height = Math.round(h * dpr); }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
    drawLayers(core, ctx, proj());
    core.dirty = false;
    for (const b of [bIn, bOut]) b.textContent = core.lang === 'vi' ? b.dataset.vi! : b.dataset.en!;
  };
  return {
    enter: () => { canvas.style.display = 'block'; core.gl.style.visibility = 'hidden'; core.external = tick; core.setMode('3d'); },
    exit: () => { canvas.style.display = 'none'; core.gl.style.visibility = core.layers.stars ? 'visible' : 'hidden'; core.external = null; core.dirty = true; },
  };
}
