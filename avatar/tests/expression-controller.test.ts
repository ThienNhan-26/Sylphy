import { describe, expect, it } from 'vitest';
import { VrmExpressionController } from '../src/expression-controller';

class FakeExpressionManager {
  values = new Map<string, number>();
  setValue(name: string, weight: number): void { this.values.set(name, weight); }
}

describe('VrmExpressionController', () => {
  it('initializes only expressions available on the model', () => {
    const manager = new FakeExpressionManager();
    const controller = new VrmExpressionController(manager, ['happy', 'blink']);
    expect(controller.availableNames.sort()).toEqual(['blink', 'happy']);
    expect(manager.values.get('happy')).toBe(0);
    expect(manager.values.has('sad')).toBe(false);
  });

  it('fades a mood in using renderer delta time', () => {
    const manager = new FakeExpressionManager();
    const controller = new VrmExpressionController(manager, ['happy', 'sad']);
    controller.setMood('happy', 0.2);
    controller.update(0.1);
    expect(manager.values.get('happy')).toBeCloseTo(0.5);
    controller.update(0.1);
    expect(manager.values.get('happy')).toBe(1);
  });

  it('fades the previous mood out when another mood is selected', () => {
    const manager = new FakeExpressionManager();
    const controller = new VrmExpressionController(manager, ['happy', 'sad']);
    controller.setMood('happy', 0);
    controller.setMood('sad', 0.2);
    controller.update(0.2);
    expect(manager.values.get('happy')).toBe(0);
    expect(manager.values.get('sad')).toBe(1);
  });

  it('keeps blink independent from mood', () => {
    const manager = new FakeExpressionManager();
    const controller = new VrmExpressionController(manager, ['happy', 'blink']);
    controller.setMood('happy', 0);
    controller.setBlink(true, 0);
    expect(manager.values.get('happy')).toBe(1);
    expect(manager.values.get('blink')).toBe(1);
  });

  it('allows one viseme at a time and clears mouth weights', () => {
    const manager = new FakeExpressionManager();
    const controller = new VrmExpressionController(manager, ['aa', 'ih', 'ou']);
    controller.setViseme('aa', 0.7, 0);
    expect(manager.values.get('aa')).toBe(0.7);
    controller.setViseme('ih', 0.8, 0);
    expect(manager.values.get('aa')).toBe(0);
    expect(manager.values.get('ih')).toBe(0.8);
    controller.setViseme(null, 0, 0);
    expect(manager.values.get('ih')).toBe(0);
  });

  it('silently ignores optional expressions missing from a model', () => {
    const manager = new FakeExpressionManager();
    const controller = new VrmExpressionController(manager, ['happy']);
    expect(() => controller.setMood('surprised', 0)).not.toThrow();
    expect(manager.values.get('happy')).toBe(0);
  });

  it('clear fades every available expression to zero', () => {
    const manager = new FakeExpressionManager();
    const controller = new VrmExpressionController(manager, ['happy', 'blink', 'aa']);
    controller.setMood('happy', 0);
    controller.setBlink(true, 0);
    controller.setViseme('aa', 1, 0);
    controller.clear(0);
    expect([...manager.values.values()].every((value) => value === 0)).toBe(true);
  });

  it('rejects invalid delta time and expression weights', () => {
    const manager = new FakeExpressionManager();
    const controller = new VrmExpressionController(manager, ['aa']);
    expect(() => controller.update(-1)).toThrow();
    expect(() => controller.setViseme('aa', 1.5)).toThrow();
  });
});
