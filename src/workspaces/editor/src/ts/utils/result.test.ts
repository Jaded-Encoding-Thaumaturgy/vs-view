import { describe, expect, expectTypeOf, it, vi } from "vitest";

import { Err, Ok, Result, ResultAsync } from "./result";

describe("Result utility", () => {
  describe("type inference", () => {
    it("should infer correct types for ok and err variants", () => {
      expectTypeOf(Result.ok(42)).toEqualTypeOf<Ok<number, never>>();
      expectTypeOf(Result.err("fail")).toEqualTypeOf<Err<string>>();
    });
  });

  describe("ok & err constructors", () => {
    it("should create an ok variant", () => {
      const res = Result.ok(42);
      expect(res.ok).toBe(true);
      expect(res.value).toBe(42);
      expect(res).toBeInstanceOf(Ok);
    });

    it("should create an err variant", () => {
      const err = new Error("Failed");
      const res = Result.err(err);
      expect(res.ok).toBe(false);
      expect(res.error).toBe(err);
      expect(res).toBeInstanceOf(Err);
    });
  });

  describe("instance method chaining: map & mapErr", () => {
    it("should map value on ok variant", () => {
      const res = Result.ok(10).map((n) => n * 2);
      expect(res.unwrap()).toBe(20);
    });

    it("should leave err variant untouched on map", () => {
      const err = new Error("Error");
      const res = Result.err(err).map((n: number) => n * 2);
      expect(res.ok).toBe(false);
      expect(res.unwrapOr(0)).toBe(0);
    });

    it("should map error on err variant", () => {
      const res = Result.err("simple error").mapErr((e) => e.toUpperCase());
      expect(res.match({ ok: () => "", err: (e) => e })).toBe("SIMPLE ERROR");
    });

    it("should leave ok variant untouched on mapErr", () => {
      const res = Result.ok("success").mapErr((e: string) => e.toUpperCase());
      expect(res.unwrap()).toBe("success");
    });
  });

  describe("instance method chaining: andThen", () => {
    it("should chain ok results", () => {
      const res = Result.ok(5).andThen((val) => Result.ok(val * 3));
      expect(res.unwrap()).toBe(15);
    });

    it("should short circuit on err in andThen chain", () => {
      const res = Result.ok(5).andThen(() => Result.err("chained error"));
      expect(res.ok).toBe(false);
      expect(res.unwrapOr(0)).toBe(0);
    });
  });

  describe("tap", () => {
    it("should invoke side-effect function on ok variant", () => {
      const spy = vi.fn();
      const res = Result.ok("payload").tap(spy);

      expect(spy).toHaveBeenCalledOnce();
      expect(spy).toHaveBeenCalledWith("payload");
      expect(res.unwrap()).toBe("payload");
    });

    it("should not invoke side-effect function on err variant", () => {
      const spy = vi.fn();
      const res = Result.err(new Error("fail")).tap(spy);

      expect(spy).not.toHaveBeenCalled();
      expect(res.ok).toBe(false);
    });
  });

  describe("unwrap & unwrapOr", () => {
    it("should return value on ok for unwrap", () => {
      expect(Result.ok("hello").unwrap()).toBe("hello");
    });

    it("should throw error on err for unwrap", () => {
      expect(() => Result.err(new Error("boom")).unwrap()).toThrow("boom");
    });

    it("should return fallback on err for unwrapOr", () => {
      expect(Result.err("fail").unwrapOr("fallback")).toBe("fallback");
    });
  });

  describe("toPlain serialization", () => {
    it("should convert Ok to plain object", () => {
      expect(Result.ok(123).toPlain()).toEqual({ ok: true, value: 123 });
    });

    it("should convert Err to plain object", () => {
      expect(Result.err("err").toPlain()).toEqual({ ok: false, error: "err" });
    });
  });

  describe("match", () => {
    it("should execute ok matcher for ok variant", () => {
      const res = Result.ok(5);
      const out = res.match({
        ok: (val) => `value: ${val}`,
        err: (err) => `error: ${err}`,
      });
      expect(out).toBe("value: 5");
    });

    it("should execute err matcher for err variant", () => {
      const res = Result.err("failed");
      const out = res.match({
        ok: (val) => `value: ${val}`,
        err: (err) => `error: ${err}`,
      });
      expect(out).toBe("error: failed");
    });
  });

  describe("fromThrowable", () => {
    it("should return ok result when function succeeds", () => {
      expect(Result.fromThrowable(() => JSON.parse('{"a":1}')).toPlain()).toEqual({
        ok: true,
        value: { a: 1 },
      });
    });

    it("should return err result when function throws Error instance", () => {
      const res = Result.fromThrowable(() => {
        throw new Error("Parse error");
      });
      expect(res.toPlain()).toEqual({ ok: false, error: new Error("Parse error") });
    });

    it("should convert non-Error throws into Error instances", () => {
      const res = Result.fromThrowable(() => {
        // eslint-disable-next-line @typescript-eslint/only-throw-error
        throw "String error";
      });
      expect(res.toPlain()).toEqual({ ok: false, error: new Error("String error") });
    });

    it("should use custom error mapper when provided", () => {
      const res = Result.fromThrowable(
        () => {
          throw new Error("Original");
        },
        (e) => `Custom: ${(e as Error).message}`,
      );
      expect(res.toPlain()).toEqual({ ok: false, error: "Custom: Original" });
    });
  });

  describe("fromPromise", () => {
    it("should handle resolved promises", async () => {
      const res = await Result.fromPromise(Promise.resolve(100));
      expect(res.toPlain()).toEqual({ ok: true, value: 100 });
    });

    it("should handle async functions", async () => {
      const res = await Result.fromPromise(async () => "async result");
      expect(res.toPlain()).toEqual({ ok: true, value: "async result" });
    });

    it("should handle rejected promises", async () => {
      const res = await Result.fromPromise(Promise.reject(new Error("Network fail")));
      expect(res.toPlain()).toEqual({ ok: false, error: new Error("Network fail") });
    });

    it("should apply custom error mapper on rejection", async () => {
      const res = await Result.fromPromise(Promise.reject(new Error("Timeout")), (e) =>
        (e as Error).message.toUpperCase(),
      );
      expect(res.toPlain()).toEqual({ ok: false, error: "TIMEOUT" });
    });
  });

  describe("ResultAsync", () => {
    it("should allow fluent map, mapErr, and andThen chaining on fromPromise", async () => {
      const res = await Result.fromPromise(Promise.resolve(21))
        .map((n) => n * 2)
        .mapErr((e: Error) => e.message)
        .andThen((n) => Result.ok(`total: ${n}`));

      expect(res.unwrap()).toBe("total: 42");
    });

    it("should support async map and mapErr callbacks", async () => {
      const okRes = await ResultAsync.fromResult(Result.ok(5))
        .map(async (n) => n + 10)
        .unwrap();
      expect(okRes).toBe(15);

      const errRes = await ResultAsync.fromResult(Result.err("fail"))
        .mapErr(async (e) => `async-${e}`)
        .toPlain();
      expect(errRes).toEqual({ ok: false, error: "async-fail" });
    });

    it("should short circuit on err variant during map & andThen", async () => {
      const spyMap = vi.fn();
      const spyAndThen = vi.fn();

      const res = await Result.fromPromise(Promise.reject(new Error("initial failure")))
        .map(spyMap)
        .andThen(spyAndThen);

      expect(spyMap).not.toHaveBeenCalled();
      expect(spyAndThen).not.toHaveBeenCalled();
      expect(res.ok).toBe(false);
      expect(await res.unwrapOr("fallback")).toBe("fallback");
    });

    it("should support tap side-effects asynchronously", async () => {
      const spy = vi.fn();
      const res = await Result.fromPromise(Promise.resolve("data")).tap(async (val) => {
        spy(val);
      });

      expect(spy).toHaveBeenCalledWith("data");
      expect(res.unwrap()).toBe("data");
    });

    it("should support match pattern matching asynchronously", async () => {
      const okMatch = await Result.fromPromise(Promise.resolve(10)).match({
        ok: (val) => val * 2,
        err: () => 0,
      });
      expect(okMatch).toBe(20);

      const errMatch = await Result.fromPromise(Promise.reject(new Error("err"))).match({
        ok: () => "ok",
        err: (e) => e.message,
      });
      expect(errMatch).toBe("err");
    });

    it("should handle unwrap rejections on Err", async () => {
      const resultAsync = Result.fromPromise(Promise.reject(new Error("unwrap test")));
      await expect(resultAsync.unwrap()).rejects.toThrow("unwrap test");
    });
  });
});
