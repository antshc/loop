const SAFE = /^[\w@%+=:,./-]+$/;

/** Joins an argument list into one POSIX shell command line. */
export function join(args: readonly string[]): string {
  return args.map(quote).join(' ');
}

/** Quotes one argument for a POSIX shell, leaving safe text unquoted. */
export function quote(argument: string): string {
  if (argument === '') {
    return "''";
  }
  return SAFE.test(argument) ? argument : `'${argument.replaceAll("'", `'"'"'`)}'`;
}
