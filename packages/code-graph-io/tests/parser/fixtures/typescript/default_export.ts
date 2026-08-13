export default function makeGreeter(prefix: string) {
  return (name: string) => prefix + name;
}
