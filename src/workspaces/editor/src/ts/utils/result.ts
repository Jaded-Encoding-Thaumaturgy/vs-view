/**
 * A simple Result type for error handling.
 *
 * @template T The type of the successful value.
 * @template E The type of the error value (defaults to Error).
 */
export type Result<T, E = Error> =
  | { readonly ok: true; readonly value: T }
  | { readonly ok: false; readonly error: E };

export const Result = {
  /**
   * Creates an Ok variant containing value T.
   */
  ok<T>(value: T): Result<T, never> {
    return { ok: true, value };
  },

  /**
   * Creates an Err variant containing error E.
   */
  err<E>(error: E): Result<never, E> {
    return { ok: false, error };
  },

  /**
   * Transforms the contained value if Ok, otherwise leaves Err untouched.
   */
  map<T, E, U>(result: Result<T, E>, fn: (val: T) => U): Result<U, E> {
    return result.ok ? Result.ok(fn(result.value)) : result;
  },

  /**
   * Transforms the contained error if Err, otherwise leaves Ok untouched.
   */
  mapErr<T, E, F>(result: Result<T, E>, fn: (err: E) => F): Result<T, F> {
    return result.ok ? result : Result.err(fn(result.error));
  },

  /**
   * Runs a side-effect function with the value if Ok without modifying the Result.
   */
  tap<T, E>(result: Result<T, E>, fn: (val: T) => void): Result<T, E> {
    if (result.ok) {
      fn(result.value);
    }
    return result;
  },

  /**
   * Pattern matches over the Result state.
   */
  match<T, E, U>(result: Result<T, E>, matchers: { ok: (val: T) => U; err: (err: E) => U }): U {
    return result.ok ? matchers.ok(result.value) : matchers.err(result.error);
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
   * Wraps an asynchronous Promise or function in a Promise<Result>.
   */
  async fromPromise<T, E = Error>(
    promiseOrFn: PromiseLike<T> | (() => PromiseLike<T> | T),
    errorMapper?: (e: unknown) => E,
  ): Promise<Result<T, E>> {
    try {
      const value = typeof promiseOrFn === "function" ? await promiseOrFn() : await promiseOrFn;
      return Result.ok(value);
    } catch (e) {
      const err = errorMapper
        ? errorMapper(e)
        : ((e instanceof Error ? e : new Error(String(e))) as E);
      return Result.err(err);
    }
  },
};
