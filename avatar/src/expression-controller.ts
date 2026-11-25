/**
 * Renderer-side VRM expression controller.
 *
 * The Entity sends semantic intent later; this controller owns only local
 * presentation weights and never interprets LLM text or mutates Entity state.
 */
export type ExpressionName =
  | 'happy' | 'angry' | 'sad' | 'relaxed' | 'surprised'
  | 'blink' | 'aa' | 'ih' | 'ou' | 'ee' | 'oh';

export type ExpressionGroup = 'mood' | 'blink' | 'viseme';

export interface ExpressionManagerLike {
  setValue(name: string, weight: number): void;
  getExpression?(name: string): unknown;
}

export interface ExpressionDefinition {
  name: ExpressionName;
  group: ExpressionGroup;
}

export const EXPRESSION_DEFINITIONS: readonly ExpressionDefinition[] = [
  { name: 'happy', group: 'mood' },
  { name: 'angry', group: 'mood' },
  { name: 'sad', group: 'mood' },
  { name: 'relaxed', group: 'mood' },
  { name: 'surprised', group: 'mood' },
  { name: 'blink', group: 'blink' },
  { name: 'aa', group: 'viseme' },
  { name: 'ih', group: 'viseme' },
  { name: 'ou', group: 'viseme' },
  { name: 'ee', group: 'viseme' },
  { name: 'oh', group: 'viseme' },
];

const MOODS = new Set<ExpressionName>(['happy', 'angry', 'sad', 'relaxed', 'surprised']);
const VISEMES = new Set<ExpressionName>(['aa', 'ih', 'ou', 'ee', 'oh']);

type Channel = { current: number; target: number; speed: number };

export class VrmExpressionController {
  private readonly channels = new Map<ExpressionName, Channel>();
  private readonly available: Set<ExpressionName>;

  constructor(
    private readonly manager: ExpressionManagerLike,
    availableNames: Iterable<string>,
    private readonly defaultFadeSeconds = 0.18,
  ) {
    if (defaultFadeSeconds < 0) throw new Error('defaultFadeSeconds must be non-negative');
    this.available = new Set(
      [...availableNames].filter((name): name is ExpressionName =>
        EXPRESSION_DEFINITIONS.some((definition) => definition.name === name),
      ),
    );
    for (const name of this.available) {
      this.channels.set(name, { current: 0, target: 0, speed: this.speedFor(defaultFadeSeconds) });
      this.manager.setValue(name, 0);
    }
  }

  get availableNames(): ExpressionName[] {
    return [...this.available];
  }

  has(name: ExpressionName): boolean {
    return this.available.has(name);
  }

  /** Select one mood. Other mood presets fade out; blink and visemes are independent. */
  setMood(name: Extract<ExpressionName, 'happy' | 'angry' | 'sad' | 'relaxed' | 'surprised'> | null,
          fadeSeconds = this.defaultFadeSeconds): void {
    this.validateFade(fadeSeconds);
    for (const mood of MOODS) this.setTarget(mood, mood === name ? 1 : 0, fadeSeconds);
  }

  /** Set a transient blink weight; call setBlink(false) to reopen the eyes. */
  setBlink(closed: boolean, fadeSeconds = 0.06): void {
    this.validateFade(fadeSeconds);
    this.setTarget('blink', closed ? 1 : 0, fadeSeconds);
  }

  /** Set a single mouth viseme. Null closes the mouth by fading all visemes to zero. */
  setViseme(name: Extract<ExpressionName, 'aa' | 'ih' | 'ou' | 'ee' | 'oh'> | null,
            weight = 1, fadeSeconds = 0.06): void {
    this.validateFade(fadeSeconds);
    if (!Number.isFinite(weight) || weight < 0 || weight > 1) {
      throw new Error('viseme weight must be between 0 and 1');
    }
    for (const viseme of VISEMES) {
      this.setTarget(viseme, viseme === name ? weight : 0, fadeSeconds);
    }
  }

  clear(fadeSeconds = this.defaultFadeSeconds): void {
    this.validateFade(fadeSeconds);
    for (const name of this.available) this.setTarget(name, 0, fadeSeconds);
  }

  /** Advance fades using renderer delta time, never wall-clock timestamps. */
  update(deltaSeconds: number): void {
    if (!Number.isFinite(deltaSeconds) || deltaSeconds < 0) {
      throw new Error('deltaSeconds must be a finite non-negative number');
    }
    for (const [name, channel] of this.channels) {
      if (channel.current === channel.target) continue;
      if (channel.speed === Infinity) {
        channel.current = channel.target;
      } else {
        const step = channel.speed * deltaSeconds;
        const difference = channel.target - channel.current;
        channel.current = Math.abs(difference) <= step
          ? channel.target
          : channel.current + Math.sign(difference) * step;
      }
      this.manager.setValue(name, channel.current);
    }
  }

  private setTarget(name: ExpressionName, target: number, fadeSeconds: number): void {
    const channel = this.channels.get(name);
    if (!channel) return; // A model may not provide every optional expression.
    channel.target = target;
    channel.speed = this.speedFor(fadeSeconds);
    if (fadeSeconds === 0) {
      channel.current = target;
      this.manager.setValue(name, target);
    }
  }

  private speedFor(seconds: number): number {
    return seconds === 0 ? Infinity : 1 / seconds;
  }

  private validateFade(seconds: number): void {
    if (!Number.isFinite(seconds) || seconds < 0) {
      throw new Error('fadeSeconds must be a finite non-negative number');
    }
  }
}
