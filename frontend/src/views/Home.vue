<template>
  <div class="home">
    <el-card class="stats-card">
      <div class="stats-grid">
        <el-card shadow="hover" class="stat-item">
          <div class="stat-content">
            <div class="stat-icon critical">
              <el-icon><WarningFilled /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ stats.critical_count }}</div>
              <div class="stat-label">严重威胁</div>
            </div>
          </div>
        </el-card>
        
        <el-card shadow="hover" class="stat-item">
          <div class="stat-content">
            <div class="stat-icon warning">
              <el-icon><InfoFilled /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ stats.warning_count }}</div>
              <div class="stat-label">警告事件</div>
            </div>
          </div>
        </el-card>
        
        <el-card shadow="hover" class="stat-item">
          <div class="stat-content">
            <div class="stat-icon today">
              <el-icon><Calendar /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ stats.today_count }}</div>
              <div class="stat-label">今日事件</div>
            </div>
          </div>
        </el-card>
        
        <el-card shadow="hover" class="stat-item">
          <div class="stat-content">
            <div class="stat-icon week">
              <el-icon><Clock /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ stats.week_count }}</div>
              <div class="stat-label">本周事件</div>
            </div>
          </div>
        </el-card>
      </div>
    </el-card>
    
    <el-card class="service-card">
      <template #header>
        <div class="card-header">
          <span>服务状态 {{ currentSiteId ? `(${currentSiteId})` : '' }}</span>
          <div class="service-controls">
            <el-button
              type="primary"
              size="small"
              :disabled="serviceStatus === 'running'"
              @click="handleStart"
            >
              启动
            </el-button>
            <el-button
              type="danger"
              size="small"
              :disabled="serviceStatus === 'stopped'"
              @click="handleStop"
            >
              停止
            </el-button>
          </div>
        </div>
      </template>
      
      <div class="service-status">
        <el-tag :type="statusType" size="large">
          {{ statusText }}
        </el-tag>
      </div>
    </el-card>
  </div>
</template>

<script>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import api from '../api/index.js'
import { useSiteStore } from '../store/site.js'
import { ElMessage } from 'element-plus'

export default {
  name: 'Home',
  setup() {
    const siteStore = useSiteStore()
    const currentSiteId = computed(() => siteStore.currentSiteId)
    const stats = ref({
      critical_count: 0,
      warning_count: 0,
      today_count: 0,
      week_count: 0
    })
    
    const serviceStatus = ref('stopped')
    const loading = ref(false)
    
    const statusType = computed(() => {
      if (serviceStatus.value === 'running') return 'success'
      if (serviceStatus.value === 'stopped') return 'info'
      return 'danger'
    })
    
    const statusText = computed(() => {
      if (serviceStatus.value === 'running') return '运行中'
      if (serviceStatus.value === 'stopped') return '已停止'
      return '错误'
    })
    
    const loadStats = async () => {
      try {
        const data = await api.get('/events/stats')
        stats.value = data
      } catch (error) {
        console.error('加载统计数据失败', error)
      }
    }
    
    const loadServiceStatus = async () => {
      try {
        const data = await api.get('/service/status')
        serviceStatus.value = data.status
      } catch (error) {
        console.error('加载服务状态失败', error)
      }
    }
    
    const handleStart = async () => {
      loading.value = true
      try {
        await api.post('/service/start')
        ElMessage.success('服务已启动')
        serviceStatus.value = 'running'
      } catch (error) {
        ElMessage.error('启动失败')
      } finally {
        loading.value = false
      }
    }
    
    const handleStop = async () => {
      loading.value = true
      try {
        await api.post('/service/stop')
        ElMessage.success('服务已停止')
        serviceStatus.value = 'stopped'
      } catch (error) {
        ElMessage.error('停止失败')
      } finally {
        loading.value = false
      }
    }
    
    let pollTimer = null

    const refresh = () => {
      if (typeof document !== 'undefined' && document.hidden) return
      loadStats()
      loadServiceStatus()
    }

    watch(currentSiteId, () => {
      refresh()
    })

    onMounted(() => {
      refresh()
      pollTimer = setInterval(refresh, 15000)
    })

    onUnmounted(() => {
      if (pollTimer) {
        clearInterval(pollTimer)
        pollTimer = null
      }
    })
    
    return {
      currentSiteId,
      stats,
      serviceStatus,
      statusType,
      statusText,
      loading,
      handleStart,
      handleStop
    }
  }
}
</script>

<style scoped>
.home {
  max-width: 1200px;
  margin: 0 auto;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 20px;
}

.stat-item {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}

.stat-content {
  display: flex;
  align-items: center;
  gap: 16px;
}

.stat-icon {
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  font-size: 24px;
}

.stat-icon.critical {
  background: rgba(245, 108, 108, 0.3);
}

.stat-icon.warning {
  background: rgba(250, 140, 22, 0.3);
}

.stat-icon.today {
  background: rgba(103, 194, 58, 0.3);
}

.stat-icon.week {
  background: rgba(64, 158, 255, 0.3);
}

.stat-info {
  flex: 1;
}

.stat-value {
  font-size: 24px;
  font-weight: bold;
  margin-bottom: 4px;
}

.stat-label {
  font-size: 14px;
  opacity: 0.8;
}

.service-card {
  margin-top: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.service-status {
  text-align: center;
  padding: 20px;
}
</style>

