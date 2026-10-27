/** The reason a promise rejects with; fails the test when it fulfils. */
export async function rejection(promise: Promise<unknown> | undefined): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error('expected the promise to reject');
}
