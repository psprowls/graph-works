export default function makeGreeter(prefix) {
  return function (name) {
    return prefix + name;
  };
}
