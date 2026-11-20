import { createRouter, createWebHashHistory } from 'vue-router'
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
        path: 'sites',
        name: 'Sites',
        component: () => import('../views/Sites.vue'),
        meta: { title: '站点管理' }
      },
      {
        path: 'sites/:siteId',
        component: () => import('../views/SiteWorkspace.vue'),
        props: true,
        children: [
          {
            path: '',
            name: 'SiteOverview',
            component: () => import('../views/SiteOverview.vue'),
            meta: { title: '站点概览' }
          },
          {
            path: 'config',
            name: 'SiteConfig',
            component: () => import('../views/Config.vue'),
            meta: { title: '站点配置' }
          },
          {
            path: 'monitor',
            name: 'SiteMonitor',
            component: () => import('../views/Monitor.vue'),
            meta: { title: '站点监控' }
          },
          {
            path: 'events',
            name: 'SiteEvents',
            component: () => import('../views/Events.vue'),
            meta: { title: '站点事件' }
          }
        ]
      },
      {
        path: 'alerts',
        name: 'Alerts',
        component: () => import('../views/Alerts.vue'),
        meta: { title: '全局告警' }
      },
      {
        path: 'config',
        redirect: '/sites'
      },
      {
        path: 'monitor',
        redirect: '/sites'
      },
      {
        path: 'events',
        redirect: '/sites'
      }
    ]
  }
]

// Hash history: refresh works even when panel Nginx lacks try_files → index.html
const router = createRouter({
  history: createWebHashHistory(),
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