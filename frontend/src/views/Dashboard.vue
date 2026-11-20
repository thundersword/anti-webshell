<template>
  <el-container class="dashboard">
    <el-header class="header">
      <div class="header-left">
        <h2>WebShell 防护系统</h2>
      </div>
      <div class="header-right">
        <el-dropdown @command="handleCommand">
          <span class="user-info">
            <el-icon><User /></el-icon>
            {{ userStore.user?.username }}
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="logout">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </el-header>
    
    <el-container>
      <el-aside width="200px" class="sidebar">
        <el-menu
          :default-active="activeMenu"
          router
          class="menu"
        >
          <el-menu-item index="/">
            <el-icon><HomeFilled /></el-icon>
            <span>首页</span>
          </el-menu-item>
          <el-menu-item index="/sites">
            <el-icon><Folder /></el-icon>
            <span>站点管理</span>
          </el-menu-item>
          <el-menu-item index="/alerts">
            <el-icon><Bell /></el-icon>
            <span>全局告警</span>
          </el-menu-item>
        </el-menu>
      </el-aside>
      
      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script>
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '../store/index.js'
import { useSiteStore } from '../store/site.js'
import api from '../api/index.js'
import { ElMessage } from 'element-plus'

export default {
  name: 'Dashboard',
  setup() {
    const route = useRoute()
    const router = useRouter()
    const userStore = useUserStore()
    const siteStore = useSiteStore()

    const activeMenu = computed(() => {
      const p = route.path
      if (p.startsWith('/sites')) return '/sites'
      if (p.startsWith('/alerts')) return '/alerts'
      return p
    })

    const loadSites = async () => {
      try {
        const data = await api.get('/sites')
        siteStore.setSites(data)
      } catch (e) {
        // ignore on first load if unauthenticated edge
      }
    }

    const handleCommand = (command) => {
      if (command === 'logout') {
        userStore.logout()
        ElMessage.success('已退出登录')
        router.push('/login')
      }
    }

    onMounted(loadSites)

    return {
      userStore,
      activeMenu,
      handleCommand
    }
  }
}
</script>

<style scoped>
.dashboard {
  height: 100vh;
}

.header {
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 20px;
}

.header-left h2 {
  margin: 0;
  font-size: 20px;
  color: #303133;
}

.header-right {
  display: flex;
  align-items: center;
}

.user-info {
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
}

.sidebar {
  background: #fff;
  border-right: 1px solid #e4e7ed;
}

.menu {
  border-right: none;
}

.main-content {
  background: #f5f7fa;
  padding: 20px;
}
</style>