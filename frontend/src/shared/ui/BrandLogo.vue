<script setup lang="ts">
import logo from '../assets/youtube-logo.svg';
</script>
<template>
  <span class="brand"
    ><img :src="logo" width="93" height="20" alt="YouTube" /><sup>RU</sup></span
  >
</template>
<style scoped lang="scss">
.brand {
  display: inline-flex;
  align-items: center;
  height: 40px;
  white-space: nowrap;
  color: var(--color-text);
  position: relative;
  sup {
    font-size: 10px;
    color: var(--color-muted);
    position: absolute;
    top: 3px;
    right: -15px;
    letter-spacing: 0;
  }
}
@media (max-width: 767px) {
  .brand sup {
    display: none;
  }
}
</style>
