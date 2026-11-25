
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import {
  VrmExpressionController,
  type ExpressionName,
} from './expression-controller';
import {
  HumanoidRig,
  type HumanoidRigReport,
} from './humanoid-rig';
import { AnimationClipRegistry } from './animation-clip-registry';
import {
  BehaviorVrmRuntime,
  type AvatarBehaviorName,
  type PresentationFrameLike,
} from './behavior-vrm-runtime';

type VrmLike = {
  scene: THREE.Object3D;
  update: (deltaSeconds: number) => void;
  humanoid?: {
    getNormalizedBoneNode: (name: string) => THREE.Object3D | null;
  };
  expressionManager?: {
    setValue: (name: string, weight: number) => void;
    getExpression?: (name: string) => unknown;
  };
};

export type ModelInfo = {
  name: string;
  metaName: string;
  version: string;
  expressions: string[];
  humanoid: HumanoidRigReport | null;
};

export class VrmViewer {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene: THREE.Scene;
  private readonly camera: THREE.PerspectiveCamera;
  private readonly controls: OrbitControls;
  private readonly clock = new THREE.Clock();
  private readonly loader = new GLTFLoader();

  private vrm: VrmLike | null = null;
  private expressionController: VrmExpressionController | null = null;
  private humanoidRig: HumanoidRig | null = null;
  private animationRegistry: AnimationClipRegistry | null = null;
  private behaviorRuntime: BehaviorVrmRuntime | null = null;

  private frameHandle = 0;
  private disposed = false;
  private movingDesktopWindow = false;
  private zoomTargetMode: 'full-body' | 'face' = 'full-body';
  private fullBodyDistance = 5;

  private fpsFrames = 0;
  private fpsElapsed = 0;
  private fpsValue = 0;

  private onFps?: (fps: number) => void;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    onFps?: (fps: number) => void,
  ) {
    this.onFps = onFps;

    // Transparent WebGL renderer.
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
      powerPreference: 'high-performance',
    });

    this.renderer.setPixelRatio(
      Math.min(window.devicePixelRatio, 2),
    );

    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.0;
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.setClearAlpha(0);

    // Scene.
    this.scene = new THREE.Scene();
    this.scene.background = null;

    // Camera.
    this.camera = new THREE.PerspectiveCamera(
      30,
      1,
      0.01,
      100,
    );

    this.camera.position.set(0, 1.35, 5);

    // Lighting.
    this.scene.add(
      new THREE.HemisphereLight(
        0xdce3ff,
        0x4b4058,
        2.0,
      ),
    );

    const key = new THREE.DirectionalLight(
      0xffe6d2,
      2.5,
    );

    key.position.set(1.5, 3, 2.5);
    this.scene.add(key);

    const rim = new THREE.DirectionalLight(
      0x9f9cff,
      1.4,
    );

    rim.position.set(-2, 2, -2);
    this.scene.add(rim);

    // Mouse interaction.
    this.controls = new OrbitControls(
      this.camera,
      this.renderer.domElement,
    );

    this.controls.target.set(0, 1.05, 0);

    this.controls.enableRotate = true;
    this.controls.enableZoom = true;
    this.controls.enablePan = false;

    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.07;

    this.controls.rotateSpeed = 0.8;
    this.controls.zoomSpeed = 0.8;

    this.controls.minDistance = 1.2;
    this.controls.maxDistance = 15;

    this.controls.minPolarAngle = 0.15;
    this.controls.maxPolarAngle = Math.PI * 0.85;

    // Right-button drag moves the desktop window.
    // Plain left-drag remains owned by OrbitControls for avatar rotation.
    this.canvas.addEventListener(
      'pointerdown',
      this.handlePointerDown,
      true,
    );
    this.canvas.addEventListener(
      'pointermove',
      this.handlePointerMove,
      true,
    );
    this.canvas.addEventListener(
      'pointerup',
      this.handlePointerUp,
      true,
    );
    this.canvas.addEventListener(
      'pointercancel',
      this.handlePointerUp,
      true,
    );
    this.canvas.addEventListener(
      'lostpointercapture',
      this.handlePointerUp,
      true,
    );
    this.canvas.addEventListener(
      'contextmenu',
      this.handleContextMenu,
      true,
    );
    // Re-aim the orbit pivot at the VRM head before OrbitControls applies zoom.
    this.canvas.addEventListener('wheel', this.handleWheel, true);
    // Double-click is a convenient reset in the embedded companion viewport.
    this.canvas.addEventListener('dblclick', this.handleDoubleClick);

    // VRM loader.
    this.loader.register(
      (parser) => new VRMLoaderPlugin(parser),
    );

    this.resize();

    window.addEventListener('resize', this.resize);

    this.animate();
  }

  private handlePointerDown = (event: PointerEvent): void => {
    if (
      event.button !== 2 ||
      !window.sylphyDesktop
    ) {
      return;
    }

    // Prevent the browser context menu and OrbitControls from handling this drag.
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    this.movingDesktopWindow = true;

    try {
      this.canvas.setPointerCapture(event.pointerId);
    } catch {
      // Pointer capture may be unavailable during window deactivation.
    }

    window.sylphyDesktop.beginWindowMove(
      event.screenX,
      event.screenY,
    );
  };

  private handleContextMenu = (event: MouseEvent): void => {
    event.preventDefault();
    event.stopPropagation();
  };

  private handleWheel = (event: WheelEvent): void => {
    if (!this.vrm) return;

    if (event.deltaY < 0 && this.zoomTargetMode === 'full-body') {
      const head = this.vrm.humanoid?.getNormalizedBoneNode('head');
      if (!head) return;

      this.vrm.scene.updateMatrixWorld(true);
      head.getWorldPosition(this.controls.target);
      this.controls.target.y += 0.025;
      this.zoomTargetMode = 'face';
      this.controls.update();
      return;
    }

    if (event.deltaY > 0 && this.zoomTargetMode === 'face') {
      const distanceFromFace = this.camera.position.distanceTo(this.controls.target);
      if (distanceFromFace >= this.fullBodyDistance * 0.92) {
        this.resetCamera();
      }
    }
  };

  private handleDoubleClick = (): void => {
    this.resetCamera();
  };

  private handlePointerMove = (event: PointerEvent): void => {
    if (!this.movingDesktopWindow) return;

    event.preventDefault();
    event.stopPropagation();

    window.sylphyDesktop?.moveWindow(
      event.screenX,
      event.screenY,
    );
  };

  private handlePointerUp = (event: PointerEvent): void => {
    if (!this.movingDesktopWindow) return;

    this.movingDesktopWindow = false;
    window.sylphyDesktop?.endWindowMove();

    try {
      if (this.canvas.hasPointerCapture(event.pointerId)) {
        this.canvas.releasePointerCapture(event.pointerId);
      }
    } catch {
      // Ignore capture cleanup errors.
    }
  };

  private resize = (): void => {
    if (this.disposed) return;

    const width = Math.max(
      1,
      this.canvas.clientWidth,
    );

    const height = Math.max(
      1,
      this.canvas.clientHeight,
    );

    this.renderer.setSize(width, height, false);

    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  };

  async load(
    url: string,
    name: string,
  ): Promise<ModelInfo> {
    const gltf = await this.loader.loadAsync(url);

    const next = gltf.userData.vrm as VrmLike | undefined;

    if (!next) {
      throw new Error(
        'The selected file loaded as glTF but contains no VRM extension.',
      );
    }

    const previous = this.vrm;

    this.behaviorRuntime?.dispose();
    this.behaviorRuntime = null;

    this.animationRegistry = null;

    this.vrm = next;

    this.scene.add(next.scene);

    // Default VRM orientation.
    // Camera is positioned on the positive Z side.
    next.scene.rotation.y = 0;

    this.fitCamera(next.scene);

    if (previous) {
      this.scene.remove(previous.scene);
      VRMUtils.deepDispose(previous.scene);
    }

    const meta = (
      next as VrmLike & {
        meta?: {
          name?: string;
          metaVersion?: string;
        };
      }
    ).meta;

    const expressionNames = [
      'happy',
      'angry',
      'sad',
      'relaxed',
      'surprised',
      'blink',
      'aa',
      'ih',
      'ou',
      'ee',
      'oh',
    ];

    const expressions = expressionNames.filter((key) => {
      try {
        return Boolean(
          next.expressionManager?.getExpression?.(key),
        );
      } catch {
        return false;
      }
    });

    // Some VRM files expose presets without getExpression.
    const canInspectExpressions =
      typeof next.expressionManager?.getExpression === 'function';

    const detected = canInspectExpressions
      ? expressions
      : expressionNames.filter(
          () => Boolean(next.expressionManager),
        );

    this.expressionController = next.expressionManager
      ? new VrmExpressionController(
          next.expressionManager,
          detected,
        )
      : null;

    this.humanoidRig = next.humanoid
      ? new HumanoidRig(next.humanoid)
      : null;

    const animationRegistry = new AnimationClipRegistry(
      next.scene,
      gltf.animations ?? [],
    );

    this.animationRegistry = animationRegistry;

    this.behaviorRuntime = this.humanoidRig
      ? new BehaviorVrmRuntime(
          this.humanoidRig,
          animationRegistry,
          this.expressionController,
        )
      : null;

    return {
      name,
      metaName: meta?.name?.trim() || name,
      version: meta?.metaVersion || 'unknown',
      expressions,
      humanoid: this.humanoidRig?.report ?? null,
    };
  }

  /**
   * Automatically frame the avatar.
   * Target size: approximately 45% of viewport height.
   */
  private fitCamera(model: THREE.Object3D): void {
    model.updateMatrixWorld(true);

    const bounds = new THREE.Box3().setFromObject(model);

    if (bounds.isEmpty()) {
      this.resetCamera();
      return;
    }

    const size = bounds.getSize(
      new THREE.Vector3(),
    );

    const center = bounds.getCenter(
      new THREE.Vector3(),
    );

    const verticalFov = THREE.MathUtils.degToRad(
      this.camera.fov,
    );

    const halfFov = Math.tan(verticalFov / 2);

    // The desktop companion uses a compact portrait window; frame the model
    // smaller there to preserve comfortable margins without changing the wide browser-preview composition.
    const isPortraitCompanion = this.camera.aspect < 1;
    const targetHeightRatio = isPortraitCompanion ? 0.54 : 0.45;
    const targetWidthRatio = isPortraitCompanion ? 0.50 : 0.55;

    const distanceForHeight =
      size.y /
      (2 * halfFov * targetHeightRatio);

    const distanceForWidth =
      size.x /
      (
        2 *
        halfFov *
        this.camera.aspect *
        targetWidthRatio
      );

    const distance = Math.max(
      distanceForHeight,
      distanceForWidth,
      1.5,
    );

    this.controls.target.copy(center);
    this.zoomTargetMode = 'full-body';
    this.fullBodyDistance = distance;

    this.camera.position.set(
      center.x,
      center.y + size.y * 0.025,
      center.z + distance,
    );

    this.camera.near = Math.max(
      0.01,
      distance / 100,
    );

    this.camera.far = Math.max(
      100,
      distance * 20,
    );

    this.camera.updateProjectionMatrix();
    this.controls.update();
  }

  setMood(
    name: Extract<
      ExpressionName,
      'happy' | 'angry' | 'sad' | 'relaxed' | 'surprised'
    > | null,
  ): void {
    this.expressionController?.setMood(name);
  }

  setBlink(closed: boolean): void {
    this.expressionController?.setBlink(closed);
  }

  setViseme(
    name: Extract<
      ExpressionName,
      'aa' | 'ih' | 'ou' | 'ee' | 'oh'
    > | null,
    weight = 1,
  ): void {
    this.expressionController?.setViseme(
      name,
      weight,
    );
  }

  /** Write only the mouth/viseme channel from an authoritative playback RMS. */
  setMouthOpen(weight: number): void {
    if (!Number.isFinite(weight)) return;
    const clamped = Math.max(0, Math.min(weight, 1));
    this.expressionController?.setViseme(
      clamped > 0.001 ? 'aa' : null,
      clamped,
      0,
    );
  }

  resetExpressions(): void {
    this.expressionController?.clear();
  }

  applyBehavior(
    behavior: AvatarBehaviorName,
  ): void {
    this.behaviorRuntime?.applyBehavior(behavior);
  }

  applyPresentationFrame(
    frame: PresentationFrameLike,
  ): void {
    this.behaviorRuntime?.applyPresentationFrame(frame);
  }

  setGaze(
    yaw: number,
    pitch: number,
  ): void {
    this.behaviorRuntime?.setGaze(yaw, pitch);
  }

  get animationClipNames(): string[] {
    return this.animationRegistry?.names ?? [];
  }

  playAnimationClip(
    name: string,
    region: 'body' | 'head' | 'eyes' | 'face' | 'mouth' = 'body',
  ): boolean {
    return (
      this.animationRegistry?.play(name, {
        channel: region,
        loop: true,
        fadeInSeconds: 0.2,
      }) ?? false
    );
  }

  stopAnimationClip(
    name: string,
    region: 'body' | 'head' | 'eyes' | 'face' | 'mouth' = 'body',
  ): void {
    this.animationRegistry?.stop(
      name,
      0.18,
      region,
    );
  }

  resetCamera(): void {
    if (this.vrm) {
      this.fitCamera(this.vrm.scene);
      return;
    }

    this.camera.position.set(0, 1.35, 5);

    this.controls.target.set(
      0,
      1.05,
      0,
    );

    this.controls.update();
  }

  unload(): void {
    if (!this.vrm) return;

    this.scene.remove(this.vrm.scene);

    this.behaviorRuntime?.dispose();

    VRMUtils.deepDispose(this.vrm.scene);

    this.behaviorRuntime = null;
    this.animationRegistry = null;
    this.vrm = null;
    this.expressionController = null;
    this.humanoidRig = null;
  }

  get loaded(): boolean {
    return this.vrm !== null;
  }

  private animate = (): void => {
    if (this.disposed) return;

    this.frameHandle = requestAnimationFrame(
      this.animate,
    );

    const delta = Math.min(
      this.clock.getDelta(),
      0.05,
    );

    this.expressionController?.update(delta);
    this.behaviorRuntime?.update(delta);
    this.vrm?.update(delta);

    this.controls.update();

    this.renderer.render(
      this.scene,
      this.camera,
    );

    this.fpsFrames += 1;
    this.fpsElapsed += delta;

    if (this.fpsElapsed >= 0.5) {
      this.fpsValue = Math.round(
        this.fpsFrames / this.fpsElapsed,
      );

      this.onFps?.(this.fpsValue);

      this.fpsFrames = 0;
      this.fpsElapsed = 0;
    }
  };

  dispose(): void {
    this.disposed = true;

    cancelAnimationFrame(this.frameHandle);

    window.removeEventListener(
      'resize',
      this.resize,
    );

    this.canvas.removeEventListener(
      'pointerdown',
      this.handlePointerDown,
      true,
    );
    this.canvas.removeEventListener(
      'pointermove',
      this.handlePointerMove,
      true,
    );
    this.canvas.removeEventListener(
      'pointerup',
      this.handlePointerUp,
      true,
    );
    this.canvas.removeEventListener(
      'pointercancel',
      this.handlePointerUp,
      true,
    );
    this.canvas.removeEventListener(
      'lostpointercapture',
      this.handlePointerUp,
      true,
    );
    this.canvas.removeEventListener(
      'contextmenu',
      this.handleContextMenu,
      true,
    );
    this.canvas.removeEventListener('wheel', this.handleWheel, true);
    this.canvas.removeEventListener('dblclick', this.handleDoubleClick);

    if (this.movingDesktopWindow) {
      this.movingDesktopWindow = false;
      window.sylphyDesktop?.endWindowMove();
    }

    this.unload();

    this.controls.dispose();
    this.renderer.dispose();
  }
}
