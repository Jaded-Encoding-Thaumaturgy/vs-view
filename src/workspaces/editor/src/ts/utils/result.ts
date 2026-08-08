export class Ok<T, E = never> {
  readonly ok = true as const;
  constructor(readonly value: T) {}

  /**
   * Transforms the contained value if Ok, otherwise leaves Err untouched.
   */
  map<U>(fn: (val: T) => U): Result<U, E> {
    return new Ok(fn(this.value));
  }

  /**
   * Transforms the contained error if Err, otherwise leaves Ok untouched.
   */
  mapErr<F>(_fn: (err: E) => F): Result<T, F> {
    return new Ok<T, F>(this.value);
  }

  /**
   * Chains another fallible operation if Ok, returning its Result.
   */
  andThen<U, F = E>(fn: (val: T) => Result<U, F>): Result<U, E | F> {
    return fn(this.value);
  }

  /**
   * Runs a side-effect function with the value if Ok without modifying the Result.
   */
  tap(fn: (val: T) => void): this {
    fn(this.value);
    return this;
  }

  /**
   * Pattern matches over the Result state.
   */
  match<U>(matchers: { ok: (val: T) => U; err: (err: E) => U }): U {
    return matchers.ok(this.value);
  }

  /**
   * Returns the contained Ok value, or throws the error if Err.
   */
  unwrap(): T {
    return this.value;
  }

  /**
   * Returns the contained Ok value, or the provided fallback value if Err.
   */
  unwrapOr<U>(_fallback: U): T {
    return this.value;
  }

  /**
   * Converts the Result into a plain JSON-serializable object.
   */
  toPlain() {
    return { ok: true as const, value: this.value };
  }
}

export class Err<E = Error> {
  readonly ok = false as const;
  constructor(readonly error: E) {}

  /**
   * Transforms the contained value if Ok, otherwise leaves Err untouched.
   */
  map<U>(_fn: (val: never) => U): Result<U, E> {
    return this as unknown as Result<U, E>;
  }

  /**
   * Transforms the contained error if Err, otherwise leaves Ok untouched.
   */
  mapErr<F>(fn: (err: E) => F): Result<never, F> {
    return new Err<F>(fn(this.error));
  }

  /**
   * Chains another fallible operation if Ok, returning its Result.
   */
  andThen<U, F = E>(_fn: (val: never) => Result<U, F>): Result<U, E | F> {
    return this as unknown as Result<U, E | F>;
  }

  /**
   * Runs a side-effect function with the value if Ok without modifying the Result.
   */
  tap(_fn: (val: never) => void): this {
    return this;
  }

  /**
   * Pattern matches over the Result state.
   */
  match<U>(matchers: { ok: (val: never) => U; err: (err: E) => U }): U {
    return matchers.err(this.error);
  }

  /**
   * Returns the contained Ok value, or throws the error if Err.
   */
  unwrap(): never {
    if (this.error instanceof Error) {
      throw this.error;
    }
    throw new Error(String(this.error));
  }

  /**
   * Returns the contained Ok value, or the provided fallback value if Err.
   */
  unwrapOr<U>(fallback: U): U {
    return fallback;
  }

  /**
   * Converts the Result into a plain JSON-serializable object.
   */
  toPlain() {
    return { ok: false as const, error: this.error };
  }
}

export type Result<T, E = Error> = Ok<T, E> | Err<E>;

export class ResultAsync<T, E = Error> implements PromiseLike<Result<T, E>> {
  /**
   * Creates a ResultAsync from an existing Result or Promise<Result>.
   */
  static fromResult<T, E = Error>(
    result: Result<T, E> | PromiseLike<Result<T, E>>,
  ): ResultAsync<T, E> {
    return new ResultAsync(Promise.resolve(result));
  }

  constructor(private readonly promise: Promise<Result<T, E>>) {}

  /**
   * Implements PromiseLike to allow awaiting the ResultAsync directly.
   */
  then<R1 = Result<T, E>, R2 = never>(
    onfulfilled?: ((value: Result<T, E>) => R1 | PromiseLike<R1>) | null,
    onrejected?: ((reason: unknown) => R2 | PromiseLike<R2>) | null,
  ): Promise<R1 | R2> {
    return this.promise.then(onfulfilled, onrejected);
  }

  /**
   * Transforms the contained value if Ok, otherwise leaves Err untouched.
   */
  map<U>(fn: (val: T) => U | PromiseLike<U>): ResultAsync<U, E> {
    return new ResultAsync<U, E>(
      this.promise.then(async (res): Promise<Result<U, E>> => {
        if (!res.ok) return res as unknown as Result<U, E>;
        return Result.ok(await fn(res.value));
      }),
    );
  }

  /**
   * Transforms the contained error if Err, otherwise leaves Ok untouched.
   */
  mapErr<F>(fn: (err: E) => F | PromiseLike<F>): ResultAsync<T, F> {
    return new ResultAsync<T, F>(
      this.promise.then(async (res): Promise<Result<T, F>> => {
        if (res.ok) return Result.ok<T>(res.value);
        return Result.err<F>(await fn(res.error));
      }),
    );
  }

  /**
   * Chains another fallible operation (sync or async) if Ok.
   */
  andThen<U, F = E>(
    fn: (val: T) => Result<U, F> | ResultAsync<U, F> | PromiseLike<Result<U, F>>,
  ): ResultAsync<U, E | F> {
    return new ResultAsync<U, E | F>(
      this.promise.then(async (res): Promise<Result<U, E | F>> => {
        if (!res.ok) return res as unknown as Result<U, E | F>;
        return await fn(res.value);
      }),
    );
  }

  /**
   * Runs a side-effect function (sync or async) with the value if Ok without modifying the Result.
   */
  tap(fn: (val: T) => void | PromiseLike<void>): ResultAsync<T, E> {
    return new ResultAsync(
      this.promise.then(async (res) => {
        if (res.ok) await fn(res.value);
        return res;
      }),
    );
  }

  /**
   * Pattern matches over the Result state asynchronously.
   */
  async match<U>(matchers: {
    ok: (val: T) => U | PromiseLike<U>;
    err: (err: E) => U | PromiseLike<U>;
  }): Promise<U> {
    const res = await this.promise;
    if (res.ok) {
      return matchers.ok(res.value);
    }
    return matchers.err(res.error);
  }

  /**
   * Returns the contained Ok value, or throws the error if Err.
   */
  async unwrap(): Promise<T> {
    return (await this.promise).unwrap();
  }

  /**
   * Returns the contained Ok value, or the provided fallback value if Err.
   */
  async unwrapOr<U>(fallback: U | PromiseLike<U>): Promise<T | U> {
    const res = await this.promise;
    if (res.ok) {
      return res.value;
    }
    return fallback;
  }

  /**
   * Converts the Result into a plain JSON-serializable object asynchronously.
   */
  async toPlain(): Promise<{ ok: true; value: T } | { ok: false; error: E }> {
    return (await this.promise).toPlain();
  }
}

export const Result = {
  /**
   * Creates an Ok variant containing value T.
   */
  ok<T>(value: T): Ok<T, never> {
    return new Ok(value);
  },
  /**
   * Creates an Err variant containing error E.
   */
  err<E>(error: E): Err<E> {
    return new Err(error);
  },

  /**
   * Wraps a synchronous fallible function in a Result.
   */
  fromThrowable<T, E = Error>(fn: () => T, errorMapper?: (e: unknown) => E): Result<T, E> {
    try {
      return Result.ok(fn());
    } catch (e) {
      const err = errorMapper
        ? errorMapper(e)
        : ((e instanceof Error ? e : new Error(String(e))) as E);
      return Result.err(err);
    }
  },

  /**
   * Wraps an asynchronous Promise or function in a ResultAsync.
   */
  fromPromise<T, E = Error>(
    promiseOrFn: PromiseLike<T> | (() => PromiseLike<T> | T),
    errorMapper?: (e: unknown) => E,
  ): ResultAsync<T, E> {
    const promise = (async () => {
      try {
        const value = typeof promiseOrFn === "function" ? await promiseOrFn() : await promiseOrFn;
        return Result.ok<T>(value);
      } catch (e) {
        const err = errorMapper
          ? errorMapper(e)
          : ((e instanceof Error ? e : new Error(String(e))) as E);
        return Result.err<E>(err);
      }
    })();

    return new ResultAsync(promise);
  },
};
