<script setup lang="ts">
import { AppIcon } from '../../../shared/ui';
import {
  filterGroups,
  type FilterKey,
  type FilterValues,
} from '../model/filters';
const props = defineProps<{ values: FilterValues }>();
const emit = defineEmits<{ select: [key: FilterKey, value: string] }>();
function selected(key: FilterKey, value: string) {
  const actual = props.values[key];
  return Array.isArray(actual) ? actual.includes(value) : actual === value;
}
</script>
<template>
  <div class="filters">
    <div v-for="group in filterGroups" :key="group.key" class="filters__group">
      <h2>{{ group.title }}</h2>
      <button
        v-for="option in group.options"
        :key="option[0]"
        type="button"
        :data-filter="group.key + ':' + option[0]"
        :aria-pressed="selected(group.key, option[0])"
        @click="emit('select', group.key, option[0])"
      >
        {{ option[1]
        }}<AppIcon v-if="selected(group.key, option[0])" name="close" />
      </button>
    </div>
  </div>
</template>
<style scoped lang="scss">
.filters {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 32px;
  padding: 12px 0 28px;
  h2 {
    text-transform: uppercase;
    font-size: 12px;
    font-weight: 500;
    border-bottom: 1px solid var(--color-border);
    padding-bottom: 16px;
    margin: 0 0 12px;
  }
  button {
    width: 100%;
    border: 0;
    background: transparent;
    color: var(--color-muted);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 4px;
    min-height: 31px;
    font-size: 14px;
    text-align: left;
    padding: 4px 0;
    cursor: pointer;
    &[aria-pressed='true'] {
      color: var(--color-text);
      font-weight: 500;
    }
    svg {
      width: 16px;
      height: 16px;
      flex-shrink: 0;
    }
  }
}
@media (max-width: 767px) {
  .filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 24px 16px;
    button {
      min-height: 44px;
    }
  }
}
</style>
