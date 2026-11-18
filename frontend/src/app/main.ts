import { createApp } from 'vue';
import { createPinia } from 'pinia';
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query';
import { createRouter, createWebHistory } from 'vue-router';
import App from './App.vue';
import { HomePage } from '../pages/home';
import { LoginPage } from '../pages/login';
import { RegisterPage } from '../pages/register';
import { SearchPage } from '../pages/search';
import { NotFoundPage } from '../pages/not-found';
import { useSession } from '../entities/session';
import { configureApi } from '../shared/api';
import './styles.scss';

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: HomePage },
    { path: '/results', component: SearchPage },
    { path: '/playlist', component: () => import('../pages/playlist-link').then((m) => m.PlaylistLinkPage) },
    { path: '/@:tag/:tab(videos|playlists)?', component: () => import('../pages/channel').then((m) => m.ChannelPage) },
    { path: '/watch', component: () => import('../pages/watch').then((m) => m.WatchPage), meta: { watchLayout: true } },
    { path: '/studio', redirect: '/studio/content/videos' },
    { path: '/studio/content/videos', component: () => import('../pages/studio-videos').then((m) => m.StudioVideosPage), meta: { standalone: true } },
    { path: '/studio/content/playlists', component: () => import('../pages/studio-playlists').then((m) => m.StudioPlaylistsPage), meta: { standalone: true } },
    { path: '/studio/video/:videoId/edit', component: () => import('../pages/video-settings').then((m) => m.VideoSettingsPage), meta: { standalone: true } },
    { path: '/studio/upload', component: () => import('../pages/upload').then((m) => m.UploadPage), meta: { standalone: true } },
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
