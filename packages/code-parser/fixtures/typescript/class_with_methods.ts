class Greeter {
  prefix: string;
  constructor(prefix: string) {
    this.prefix = prefix;
  }
  greet(name: string): string {
    return this.prefix + name;
  }
}
