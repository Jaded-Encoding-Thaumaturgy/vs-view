import { describe, expect, expectTypeOf, it, vi } from "vitest";

import { Result } from "./result";

describe("Result utility", () => {
  describe("type inference", () => {
    it("should infer correct types for ok and err variants", () => {
      expectTypeOf(Result.ok(42)).toEqualTypeOf<Result<number, never>>();
      expectTypeOf(Result.err("fail")).toEqualTypeOf<Result<never, string>>();
    });
  });

  describe("ok & err constructors", () => {
    it("should create an ok variant", () => {
      expect(Result.ok(42)).toEqual({ ok: true, value: 42 });
    });

    it("should create an err variant", () => {
      const err = new Error("Failed");
      expect(Result.err(err)).toEqual({ ok: false, error: err });
    });
  });

  describe("map & mapErr", () => {
    it("should map value on ok variant", () => {
      const res = Result.ok(10);
      expect(Result.map(res, (n) => n * 2)).toEqual(Result.ok(20));
    });

    it("should leave err variant untouched on map", () => {
      const err = new Error("Error");
      const res = Result.err(err);
      expect(Result.map(res, (n: number) => n * 2)).toEqual(Result.err(err));
    });

    it("should map error on err variant", () => {
      const res = Result.err("simple error");
      expect(Result.mapErr(res, (e) => e.toUpperCase())).toEqual(Result.err("SIMPLE ERROR"));
    });

    it("should leave ok variant untouched on mapErr", () => {
      const res = Result.ok("success");
      expect(Result.mapErr(res, (e: string) => e.toUpperCase())).toEqual(Result.ok("success"));
    });
  });

  describe("tap", () => {
    it("should invoke side-effect function on ok variant", () => {
      const spy = vi.fn();
      const res = Result.ok("payload");
      const returned = Result.tap(res, spy);

      expect(spy).toHaveBeenCalledOnce();
      expect(spy).toHaveBeenCalledWith("payload");
      expect(returned).toBe(res);
    });

    it("should not invoke side-effect function on err variant", () => {
      const spy = vi.fn();
      const res = Result.err(new Error("fail"));
      const returned = Result.tap(res, spy);

      expect(spy).not.toHaveBeenCalled();
      expect(returned).toBe(res);
    });
  });

  describe("match", () => {
    it("should execute ok matcher for ok variant", () => {
      const res = Result.ok(5);
      const out = Result.match(res, {
        ok: (val) => `value: ${val}`,
        err: (err) => `error: ${err}`,
      });
      expect(out).toBe("value: 5");
    });

    it("should execute err matcher for err variant", () => {
      const res = Result.err("failed");
      const out = Result.match(res, {
        ok: (val) => `value: ${val}`,
        err: (err) => `error: ${err}`,
      });
      expect(out).toBe("error: failed");
    });
  });

  describe("fromThrowable", () => {
    it("should return ok result when function succeeds", () => {
      expect(Result.fromThrowable(() => JSON.parse('{"a":1}'))).toEqual(Result.ok({ a: 1 }));
    });

    it("should return err result when function throws Error instance", () => {
      const res = Result.fromThrowable(() => {
        throw new Error("Parse error");
      });
      expect(res).toEqual(Result.err(new Error("Parse error")));
    });

    it("should convert non-Error throws into Error instances", () => {
      const res = Result.fromThrowable(() => {
        // eslint-disable-next-line @typescript-eslint/only-throw-error
        throw "String error";
      });
      expect(res).toEqual(Result.err(new Error("String error")));
    });

    it("should use custom error mapper when provided", () => {
      const res = Result.fromThrowable(
        () => {
          throw new Error("Original");
        },
        (e) => `Custom: ${(e as Error).message}`,
      );
      expect(res).toEqual(Result.err("Custom: Original"));
    });
  });

  describe("fromPromise", () => {
    it("should handle resolved promises", async () => {
      await expect(Result.fromPromise(Promise.resolve(100))).resolves.toEqual(Result.ok(100));
    });

    it("should handle async functions", async () => {
      await expect(
        Result.fromPromise(async () => {
          return "async result";
        }),
      ).resolves.toEqual(Result.ok("async result"));
    });

    it("should handle rejected promises", async () => {
      await expect(Result.fromPromise(Promise.reject(new Error("Network fail")))).resolves.toEqual(
        Result.err(new Error("Network fail")),
      );
    });

    it("should apply custom error mapper on rejection", async () => {
      await expect(
        Result.fromPromise(Promise.reject(new Error("Timeout")), (e) =>
          (e as Error).message.toUpperCase(),
        ),
      ).resolves.toEqual(Result.err("TIMEOUT"));
    });
  });
});
