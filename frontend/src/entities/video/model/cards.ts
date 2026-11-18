import { isIdentifier, type FeedCardResponse } from '../../../shared/api';
export function validateCards<T extends { items: FeedCardResponse[] }>(
  page: T,
): T {
  if (
    page.items.some(
      (item) => !isIdentifier(item.id) || !isIdentifier(item.channel.id),
    )
  )
    throw new Error('Unsupported identifier');
  return page;
}
