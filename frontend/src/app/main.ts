import { createApp } from 'vue';
import { createPinia } from 'pinia';
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query';
import { createRouter, createWebHistory } from 'vue-router';
import App from './App.vue';
import { HomePage } from '../pages/home';
import { LoginPage } from '../pages/login';
import { RegisterPage } from '../pages/register';
import { NotFoundPage } from '../pages/not-found';
import { useSession } from '../entities/session';
import { configureApi } from '../shared/api';
import './styles.scss';

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: HomePage },
    { path: '/login', component: LoginPage, meta: { standalone: true } },
    { path: '/register', component: RegisterPage, meta: { standalone: true } },
    { path: '/:pathMatch(.*)*', component: NotFoundPage },
  ],
  scrollBehavior: (to, from, savedPosition) => {
    if (to.fullPath.split('#')[0] === from.fullPath.split('#')[0])
      return savedPosition ?? false;
    return savedPosition ?? { top: 0 };
  },
});

const app = createApp(App);
const pinia = createPinia();
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
});
app.use(pinia).use(VueQueryPlugin, { queryClient }).use(router);
configureApi(
  () => {
    void app.runWithContext(() => useSession(pinia).expire());
  },
  () => useSession(pinia).scope,
);
app.mount('#app');
