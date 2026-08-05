export interface IResponse {
  status: number;
  body: string;
}

export type ResponseId = number;

export enum Status {
  Ok,
  Error,
}
