export class DuplicateAccountError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DuplicateAccountError";
  }
}
