import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '../router'

const api = axios.create({
  baseURL: '/api',
  timeout: 10000
})

/** Paths that are host-global — never attach current site_id / X-Site-Id. */
const GLOBAL_API_PATHS = [
  '/service/self-guard',
  '/config/alert-config',
  '/profiles',
  '/auth/',
  '/health'
]

function isGlobalApiPath(url = '') {
  const path = String(url).split('?')[0]
  return GLOBAL_API_PATHS.some(
    (prefix) => path === prefix || path.startsWith(prefix)
  )
}

// 请求拦截器
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('access_token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    const siteId = localStorage.getItem('current_site_id')
    if (siteId && !isGlobalApiPath(config.url)) {
      config.headers['X-Site-Id'] = siteId
      // Also attach as query for GET convenience when not already set
      if (config.method === 'get' || !config.method) {
        config.params = { ...(config.params || {}), site_id: config.params?.site_id || siteId }
      }
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器
api.interceptors.response.use(
  (response) => {
    return response.data
  },
  (error) => {
    if (error.response) {
      const { status, data } = error.response
      
      if (status === 401) {
        localStorage.removeItem('access_token')
        router.push('/login')
        ElMessage.error('登录已过期，请重新登录')
      } else {
        ElMessage.error(data.error || '请求失败')
      }
    }
    
    return Promise.reject(error)
  }
)

export default api

