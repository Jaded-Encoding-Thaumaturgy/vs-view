import { describe, expect, it, vi } from 'vite-plus/test';

import { Disposable, DisposableStore, toDisposable } from './disposables';

class TestComponent extends Disposable {
  public register(fn: () => void): void {
    this._disposables.add(toDisposable(fn));
  }
}

describe('Disposables utility', () => {
  describe('toDisposable', () => {
    it('should call the teardown function on dispose', () => {
      const teardown = vi.fn();
      const disposable = toDisposable(teardown);

      expect(teardown).not.toHaveBeenCalled();
      disposable.dispose();
      expect(teardown).toHaveBeenCalledOnce();
    });

    it('should be idempotent (teardown called only once)', () => {
      const teardown = vi.fn();
      const disposable = toDisposable(teardown);

      disposable.dispose();
      disposable.dispose();
      disposable.dispose();

      expect(teardown).toHaveBeenCalledOnce();
    });
  });

  describe('DisposableStore', () => {
    it('should track and dispose all added disposables in LIFO order', () => {
      const store = new DisposableStore();
      const callOrder: number[] = [];

      store.add(toDisposable(() => callOrder.push(1)));
      store.add(toDisposable(() => callOrder.push(2)));
      store.add(toDisposable(() => callOrder.push(3)));

      store.dispose();

      expect(callOrder).toEqual([3, 2, 1]);
    });

    it('should immediately dispose added item if store is already disposed', () => {
      const store = new DisposableStore();
      store.dispose();

      const teardown = vi.fn();
      store.add(toDisposable(teardown));

      expect(teardown).toHaveBeenCalledOnce();
    });

    it('should safely handle errors during individual resource disposal', () => {
      const store = new DisposableStore();
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

      const throwingDisposable = toDisposable(() => {
        throw new Error('Disposal error');
      });
      const normalTeardown = vi.fn();
      const normalDisposable = toDisposable(normalTeardown);

      store.add(normalDisposable);
      store.add(throwingDisposable);

      expect(() => store.dispose()).not.toThrow();
      expect(normalTeardown).toHaveBeenCalledOnce();
      expect(consoleSpy).toHaveBeenCalledOnce();

      consoleSpy.mockRestore();
    });

    it('should support Explicit Resource Management Symbol.dispose', () => {
      const store = new DisposableStore();
      const teardown = vi.fn();

      store.add(toDisposable(teardown));
      store[Symbol.dispose]();

      expect(teardown).toHaveBeenCalledOnce();
    });

    it("should dispose automatically with 'using' scope exit", () => {
      const teardown = vi.fn();
      {
        using store = new DisposableStore();
        store.add(toDisposable(teardown));
        expect(teardown).not.toHaveBeenCalled();
      }
      expect(teardown).toHaveBeenCalledOnce();
    });
  });

  describe('Disposable base class', () => {
    it('disposes tracked resources on dispose()', () => {
      const teardown = vi.fn();
      const component = new TestComponent();
      component.register(teardown);

      component.dispose();
      expect(teardown).toHaveBeenCalledOnce();
    });
  });
});
