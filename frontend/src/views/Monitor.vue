<template>
  <div class="monitor">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>实时监控日志</span>
          <el-button-group>
            <el-button :type="isWatching ? 'danger' : 'success'" @click="toggleWatch">
              {{ isWatching ? '停止监控' : '开始监控' }}
            </el-button>
            <el-button @click="clearLogs">清空日志</el-button>
          </el-button-group>
        </div>
      </template>
      
      <div class="log-container">
        <div ref="logContainer" class="log-content">
          <div
            v-for="(line, index) in logs"
            :key="index"
            :class="['log-line', getLogLevel(line)]"
          >
            {{ line }}
          </div>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import api from '../api/index.js'

export default {
  name: 'Monitor',
  setup() {
    const isWatching = ref(false)
    const logs = ref([])
    const logContainer = ref(null)
    let logInterval = null
    
    const loadLogs = async () => {
      try {
        const data = await api.get('/service/logs', { params: { lines: 200 } })
        const raw = data.logs
        const lines = Array.isArray(raw)
          ? raw.filter((line) => String(line).trim())
          : String(raw || '').split('\n').filter((line) => line.trim())
        logs.value = lines
        nextTick(() => {
          scrollToBottom()
        })
      } catch (error) {
        console.error('加载日志失败', error)
      }
    }
    
    const scrollToBottom = () => {
      if (logContainer.value) {
        logContainer.value.scrollTop = logContainer.value.scrollHeight
      }
    }
    
    const toggleWatch = () => {
      isWatching.value = !isWatching.value
      
      if (isWatching.value) {
        logInterval = setInterval(loadLogs, 5000)
      } else {
        if (logInterval) {
          clearInterval(logInterval)
          logInterval = null
        }
      }
    }
    
    const clearLogs = () => {
      logs.value = []
    }
    
    const getLogLevel = (line) => {
      if (line.includes('CRITICAL')) return 'critical'
      if (line.includes('WARNING')) return 'warning'
      if (line.includes('INFO')) return 'info'
      return ''
    }
    
    onMounted(() => {
      loadLogs()
    })
    
    onUnmounted(() => {
      if (logInterval) {
        clearInterval(logInterval)
      }
    })
    
    return {
      isWatching,
      logs,
      logContainer,
      toggleWatch,
      clearLogs,
      getLogLevel
    }
  }
}
</script>

<style scoped>
.monitor {
  max-width: 1200px;
  margin: 0 auto;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.log-container {
  height: 600px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  background: #1e1e1e;
  overflow: hidden;
}

.log-content {
  height: 100%;
  overflow-y: auto;
  padding: 10px;
  font-family: 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  color: #d4d4d4;
}

.log-line {
  white-space: pre-wrap;
  word-break: break-all;
  margin-bottom: 2px;
}

.log-line.critical {
  color: #f48771;
}

.log-line.warning {
  color: #cca700;
}

.log-line.info {
  color: #4ec9b0;
}
</style>

