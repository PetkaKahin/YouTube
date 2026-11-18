import {
  isLosslessNumber,
  LosslessNumber,
  parse,
  stringify,
} from 'lossless-json';

declare const identifierBrand: unique symbol;
export type Identifier = string & { readonly [identifierBrand]: true };

type ScalarIdentifier<T> = T extends number ? Identifier : ExactIdentifiers<T>;
export type ExactIdentifiers<T> = T extends readonly (infer Item)[]
  ? ExactIdentifiers<Item>[]
  : T extends object
    ? {
        [Key in keyof T]: Key extends 'id' | `${string}_id`
          ? ScalarIdentifier<T[Key]>
          : Key extends 'ids' | `${string}_ids`
            ? T[Key] extends readonly (infer Item)[]
              ? ScalarIdentifier<Item>[]
              : ExactIdentifiers<T[Key]>
            : ExactIdentifiers<T[Key]>;
      }
    : T;

export function isIdentifier(value: unknown): value is Identifier {
  return (
    typeof value === 'string' &&
    /^[1-9][0-9]{0,18}$/.test(value) &&
    BigInt(value) <= 9223372036854775807n
  );
}

function identifierKey(key: string) {
  return (
    key === 'id' || key === 'ids' || key.endsWith('_id') || key.endsWith('_ids')
  );
}

function decode(value: unknown, identifier = false): unknown {
  if (isLosslessNumber(value)) {
    if (identifier) {
      if (!isIdentifier(value.value))
        throw new TypeError('Invalid int64 identifier');
      return value.value;
    }
    const number = value.valueOf();
    if (
      typeof number !== 'number' ||
      !Number.isFinite(number) ||
      (Number.isInteger(number) && !Number.isSafeInteger(number))
    )
      throw new TypeError('Unsafe numeric response');
    return number;
  }
  if (Array.isArray(value))
    return value.map((item) => decode(item, identifier));
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        decode(item, identifierKey(key)),
      ]),
    );
  }
  return value;
}

export function parseApiJson(text: string): unknown {
  return decode(parse(text));
}

function encode(value: unknown, identifier = false): unknown {
  if (identifier && typeof value === 'string' && /^-?\d+$/.test(value)) {
    if (!isIdentifier(value)) throw new TypeError('Invalid int64 identifier');
    return new LosslessNumber(value);
  }
  if (identifier && typeof value === 'number' && !isIdentifier(String(value))) {
    throw new TypeError('Invalid int64 identifier');
  }
  if (
    typeof value === 'number' &&
    (!Number.isFinite(value) ||
      (Number.isInteger(value) && !Number.isSafeInteger(value)))
  )
    throw new TypeError('Unsafe numeric request');
  if (Array.isArray(value))
    return value.map((item) => encode(item, identifier));
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        encode(item, identifierKey(key)),
      ]),
    );
  }
  return value;
}

export function serializeApiJson(body: unknown): string | undefined {
  return stringify(encode(body));
}
