/** Notes attached to an error as a non-enumerable `notes` array, the way Python's `add_note` does. */

/** Appends `note` to the error's notes; false when the thrown value cannot carry notes. */
export function addNote(error: unknown, note: string): boolean {
  if (typeof error !== 'object' || error === null) {
    return false;
  }
  try {
    Object.defineProperty(error, 'notes', {
      configurable: true,
      enumerable: false,
      value: [...notesOf(error), note],
      writable: true,
    });
  } catch {
    return false;
  }
  return true;
}

export function notesOf(error: unknown): readonly string[] {
  if (typeof error !== 'object' || error === null) {
    return [];
  }
  const notes: unknown = Reflect.get(error, 'notes');
  return Array.isArray(notes) ? notes.filter((note): note is string => typeof note === 'string') : [];
}
