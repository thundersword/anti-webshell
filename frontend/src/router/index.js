import { createRouter, createWebHistory } from 'vue-router'
import Login from '../views/Login.vue'
import Dashboard from '../views/Dashboard.vue'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: Login,
    meta: { requiresAuth: false }
  },
  {
    path: '/',
    component: Dashboard,
    meta: { requiresAuth: true },
    children: [
      {
        path: '',
        name: 'Home',
        component: () => import('../views/Home.vue'),
        meta: { title: '首页' }
      },
      {
        path: '/sites',
        name: 'Sites',
        component: () => import('../views/Sites.vue'),
        meta: { title: '站点管理' }
      },
      {
        path: '/config',
        name: 'Config',
        component: () => import('../views/Config.vue'),
        meta: { title: '配置管理' }
      },
      {
        path: '/monitor',
        name: 'Monitor',
        component: () => import('../views/Monitor.vue'),
        meta: { title: '实时监控' }
      },
      {
        path: '/events',
        name: 'Events',
        component: () => import('../views/Events.vue'),
        meta: { title: '事件日志' }
      }
    ]
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 路由守卫
router.beforeEach((to, from, next) => {
  if (to.meta.requiresAuth) {
    const token = localStorage.getItem('access_token')
    if (token) {
      next()
    } else {
      next('/login')
    }
  } else {
    next()
  }
})

export default router

