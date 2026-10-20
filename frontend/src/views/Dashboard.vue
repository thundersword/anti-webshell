<template>
  <el-container class="dashboard">
    <el-header class="header">
      <div class="header-left">
        <h2>WebShell 防护系统</h2>
      </div>
      <div class="header-right">
        <el-select
          v-if="siteStore.sites.length"
          v-model="currentSiteId"
          placeholder="选择站点"
          size="small"
          style="width: 160px; margin-right: 16px"
          @change="onSiteChange"
        >
          <el-option
            v-for="s in siteStore.sites"
            :key="s.site_id"
            :label="s.name || s.site_id"
            :value="s.site_id"
          />
        </el-select>
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
          <el-menu-item index="/config">
            <el-icon><Setting /></el-icon>
            <span>配置管理</span>
          </el-menu-item>
          <el-menu-item index="/monitor">
            <el-icon><Monitor /></el-icon>
            <span>实时监控</span>
          </el-menu-item>
          <el-menu-item index="/events">
            <el-icon><Document /></el-icon>
            <span>事件日志</span>
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
import { computed, onMounted, ref, watch } from 'vue'
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
    const currentSiteId = ref(siteStore.currentSiteId)

    const activeMenu = computed(() => route.path)

    const loadSites = async () => {
      try {
        const data = await api.get('/sites')
        siteStore.setSites(data)
        currentSiteId.value = siteStore.currentSiteId
      } catch (e) {
        // ignore on first load if unauthenticated edge
      }
    }

    const onSiteChange = (val) => {
      siteStore.setCurrentSiteId(val)
      ElMessage.success(`当前站点: ${val}`)
    }

    const handleCommand = (command) => {
      if (command === 'logout') {
        userStore.logout()
        ElMessage.success('已退出登录')
        router.push('/login')
      }
    }

    watch(
      () => siteStore.currentSiteId,
      (v) => {
        currentSiteId.value = v
      }
    )

    onMounted(loadSites)

    return {
      userStore,
      siteStore,
      currentSiteId,
      activeMenu,
      onSiteChange,
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

