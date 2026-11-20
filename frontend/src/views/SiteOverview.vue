<template>
  <div class="site-overview">
    <el-card class="service-card" v-loading="loading">
      <template #header>
        <div class="card-header">
          <span>服务状态 ({{ siteId }})</span>
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
import { useRoute } from 'vue-router'
import api from '../api/index.js'
import { useSiteStore } from '../store/site.js'
import { ElMessage } from 'element-plus'

export default {
  name: 'SiteOverview',
  setup() {
    const route = useRoute()
    const siteStore = useSiteStore()

    const siteId = computed(() => route.params.siteId || siteStore.currentSiteId)
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
      } catch {
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
      } catch {
        ElMessage.error('停止失败')
      } finally {
        loading.value = false
      }
    }

    let pollTimer = null

    const refresh = () => {
      if (typeof document !== 'undefined' && document.hidden) return
      loadServiceStatus()
    }

    watch(siteId, (id) => {
      if (id) {
        siteStore.setCurrentSiteId(id)
        refresh()
      }
    })

    onMounted(() => {
      if (siteId.value) {
        siteStore.setCurrentSiteId(siteId.value)
      }
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
      siteId,
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
.service-card {
  margin-top: 12px;
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