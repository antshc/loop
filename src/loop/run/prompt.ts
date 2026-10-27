/** A prompt template with the arguments that fill it. */

const PLACEHOLDER = /\{\{\s*([A-Za-z_]\w*)\s*\}\}/g;

/** A template plus the string arguments for its `{{KEY}}` placeholders. */
export class Prompt {
  readonly args: Readonly<Record<string, string>>;
  readonly template: string;

  constructor(template: string, args: Readonly<Record<string, string>>) {
    this.template = template;
    this.args = Object.freeze({ ...args });
    Object.freeze(this);
  }

  /** Replaces each placeholder; a missing argument raises `PromptError`. */
  render(): string {
    return this.template.replaceAll(PLACEHOLDER, (_match, key: string) => {
      const value = Object.hasOwn(this.args, key) ? this.args[key] : undefined;
      if (value === undefined) {
        throw new PromptError(`missing prompt argument: ${key}`);
      }
      return value;
    });
  }
}

/** A prompt template placeholder has no matching argument. */
export class PromptError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'PromptError';
  }
}
