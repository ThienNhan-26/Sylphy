import type { VrmExpressionController } from './expression-controller';

/** Delta-time driven automatic blink scheduler; no browser timers are used. */
export class AutomaticBlinkController {
  private enabled = true;
  private elapsed = 0;
  private nextBlink = 3.6;
  private phase: 'waiting' | 'closing' | 'holding' | 'opening' = 'waiting';
  private phaseElapsed = 0;
  private seed = 0x51f15e;

  constructor(private readonly expressions: VrmExpressionController | null) {}

  setEnabled(enabled: boolean): void {
    this.enabled = enabled;
    if (!enabled) {
      this.phase = 'waiting'; this.phaseElapsed = 0;
      this.expressions?.setBlink(false, 0.08);
    }
  }

  update(deltaSeconds: number): void {
    if (!this.enabled || !this.expressions?.has('blink')) return;
    const dt = Math.min(Math.max(deltaSeconds, 0), 0.05);
    if (this.phase === 'waiting') {
      this.elapsed += dt;
      if (this.elapsed >= this.nextBlink) {
        this.elapsed = 0; this.phase = 'closing'; this.phaseElapsed = 0;
        this.expressions.setBlink(true, 0.055);
      }
      return;
    }
    this.phaseElapsed += dt;
    if (this.phase === 'closing' && this.phaseElapsed >= 0.075) {
      this.phase = 'holding'; this.phaseElapsed = 0;
    } else if (this.phase === 'holding' && this.phaseElapsed >= 0.045) {
      this.phase = 'opening'; this.phaseElapsed = 0;
      this.expressions.setBlink(false, 0.09);
    } else if (this.phase === 'opening' && this.phaseElapsed >= 0.11) {
      this.phase = 'waiting'; this.phaseElapsed = 0;
      this.nextBlink = 3.1 + this.random() * 2.1;
    }
  }

  private random(): number {
    this.seed ^= this.seed << 13; this.seed ^= this.seed >>> 17; this.seed ^= this.seed << 5;
    return (this.seed >>> 0) / 0xffffffff;
  }
}
