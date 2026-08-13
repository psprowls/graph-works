interface IFoo {
  bar(): number;
}

function callIt(f: IFoo): number {
  return f.bar();
}
