import { createApp } from 'vue';
import { createPinia } from 'pinia';
import { VueQueryPlugin } from '@tanstack/vue-query';
import { createRouter, createWebHistory } from 'vue-router';
import App from './App.vue';
import { HomePage } from '../pages/home';
import './styles.css';

const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/', component: HomePage }],
});

createApp(App).use(createPinia()).use(VueQueryPlugin).use(router).mount('#app');
