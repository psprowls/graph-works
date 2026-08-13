function identity<T>(x: T): T {
  return x;
}

class Box<T> {
  value: T;
  constructor(value: T) {
    this.value = value;
  }
}
