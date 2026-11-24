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

    <el-card class="service-card">
      <template #header>
        <span>审计</span>
      </template>
      <div class="audit-body">
        <p>auditd：{{ auditdText }}</p>
        <p>{{ rulesText }}</p>
        <p class="audit-hint">手工排除、临时停用某个目录时，只编辑 audit.local.rules，然后重新执行 sync-audit。</p>
        <pre class="audit-commands">{{ auditCommands }}</pre>
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
    const audit = ref({
      auditd: 'unknown',
      rules_loaded: false,
      sync_command: '',
      check_command: ''
    })

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

    const auditdText = computed(() => {
      const value = String(audit.value.auditd || '').toLowerCase()
      if (value === 'active') return '运行中'
      if (value === 'inactive') return '未运行'
      return '未知'
    })

    const rulesText = computed(() => (
      audit.value.rules_loaded ? '本站规则已加载' : '本站规则未加载'
    ))

    const auditCommands = computed(() => (
      [audit.value.sync_command, audit.value.check_command].join('\n')
    ))

    const loadServiceStatus = async () => {
      try {
        const data = await api.get('/service/status')
        serviceStatus.value = data.status
      } catch (error) {
        console.error('加载服务状态失败', error)
      }
    }

    const loadAuditStatus = async () => {
      try {
        const data = await api.get('/service/audit-status', { skipErrorToast: true })
        audit.value = {
          auditd: data.auditd || 'unknown',
          rules_loaded: !!data.rules_loaded,
          sync_command: data.sync_command || '',
          check_command: data.check_command || ''
        }
      } catch (error) {
        console.error('加载审计状态失败', error)
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
      loadAuditStatus()
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
      handleStop,
      auditdText,
      rulesText,
      auditCommands
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

.audit-body p {
  margin: 0 0 8px;
}

.audit-hint {
  color: #606266;
}

.audit-commands {
  margin: 12px 0 0;
  padding: 12px;
  background: #1e1e1e;
  color: #d4d4d4;
  font-size: 12px;
  white-space: pre-wrap;
}
</style>