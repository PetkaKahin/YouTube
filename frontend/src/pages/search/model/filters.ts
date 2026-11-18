import type { LocationQuery } from 'vue-router';

export const filterGroups = [
  {
    key: 'type',
    title: 'Тип',
    options: [
      ['video', 'Видео'],
      ['channel', 'Каналы'],
      ['playlist', 'Плейлисты'],
    ],
  },
  {
    key: 'duration',
    title: 'Длительность',
    options: [
      ['short', 'Менее 3 минут'],
      ['medium', '3–20 минут'],
      ['long', 'Более 20 минут'],
    ],
  },
  {
    key: 'period',
    title: 'По дате загрузки',
    options: [
      ['today', 'Сегодня'],
      ['week', 'За эту неделю'],
      ['month', 'За этот месяц'],
      ['year', 'За этот год'],
    ],
  },
  {
    key: 'feature',
    title: 'Особенности',
    options: [
      ['4k', '4K'],
      ['hd', 'HD'],
      ['subtitles', 'Субтитры'],
      ['hdr', 'HDR'],
    ],
  },
  {
    key: 'sort',
    title: 'Сортировка',
    options: [
      ['created', 'По дате создания'],
      ['updated', 'По дате изменения'],
    ],
  },
] as const;
export type FilterKey = (typeof filterGroups)[number]['key'];
export type FilterValues = {
  type?: string;
  duration?: string;
  period?: string;
  sort: string;
  feature: string[];
};

export function readFilters(query: LocationQuery) {
  const values: FilterValues = { sort: 'created', feature: [] };
  let valid = true;
  for (const group of filterGroups) {
    const raw = query[group.key];
    if (raw === undefined) continue;
    const allowed = (value: unknown): value is string =>
      typeof value === 'string' &&
      group.options.some(([option]) => value === option);
    if (group.key === 'feature') {
      const items = Array.isArray(raw) ? raw : [raw];
      if (!items.every(allowed)) valid = false;
      values.feature = [...new Set(items.filter(allowed))].sort();
    } else if (allowed(raw)) values[group.key] = raw;
    else valid = false;
  }
  return { valid, values };
}
