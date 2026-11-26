<template>
  <div class="events">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>事件日志</span>
          <el-select v-model="severityFilter" placeholder="筛选严重程度" clearable>
            <el-option label="严重" value="CRITICAL" />
            <el-option label="警告" value="WARNING" />
            <el-option label="信息" value="INFO" />
          </el-select>
        </div>
      </template>
      
      <el-table
        :data="events"
        style="width: 100%"
        v-loading="loading"
        :row-class-name="rowClassName"
        @row-click="openTimeline"
      >
        <el-table-column type="index" label="#" width="60" />
        <el-table-column prop="event_type" label="事件类型" width="150" />
        <el-table-column prop="file_path" label="文件路径" show-overflow-tooltip />
        <el-table-column prop="severity" label="严重程度" width="120">
          <template #default="{ row }">
            <el-tag :type="getSeverityType(row.severity)">
              {{ row.severity }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="action_taken" label="采取行动" width="150" />
        <el-table-column prop="created_at" label="时间" width="180" />
        <el-table-column label="追踪" width="180">
          <template #default="{ row }">
            <template v-if="canTrack(row)">
              <el-button size="small" link type="primary" @click.stop="startTrack(row, false)">追踪</el-button>
              <el-button size="small" link type="danger" @click.stop="startTrack(row, true)">追踪并隔离</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
      
      <div class="pagination">
        <el-pagination
          v-model:current-page="page"
          :page-sizes="[10, 20, 50, 100]"
          :page-size="perPage"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="handleSizeChange"
          @current-change="handlePageChange"
        />
      </div>
    </el-card>

    <el-drawer v-model="drawerVisible" title="入侵时间线" size="520px">
      <div v-loading="timelineLoading">
        <template v-if="timelineState === 'ok'">
          <el-descriptions :column="1" border>
            <el-descriptions-item label="入口">{{ timeline.entry }}</el-descriptions-item>
            <el-descriptions-item label="置信度">{{ confidenceText }}</el-descriptions-item>
            <el-descriptions-item label="来源 IP">{{ displayValue(actor.ip) }}</el-descriptions-item>
            <el-descriptions-item label="URL">{{ actorUrl }}</el-descriptions-item>
            <el-descriptions-item label="exe">{{ displayValue(actor.exe) }}</el-descriptions-item>
            <el-descriptions-item label="用户">{{ displayValue(actor.uid) }}</el-descriptions-item>
          </el-descriptions>
          <h4>缺口</h4>
          <ul v-if="gaps.length" class="gap-list">
            <li v-for="(gap, index) in gaps" :key="index">{{ gap }}</li>
          </ul>
          <p v-else>无</p>
          <h4>事件</h4>
          <ol class="event-list">
            <li v-for="(item, index) in timelineEvents" :key="index">
              <span class="event-meta">{{ item.ts }} · {{ item.source }} · {{ item.kind }}</span>
              <div>{{ item.summary }}</div>
            </li>
          </ol>
        </template>
        <template v-else-if="timelineState === 'failed'">
          <p>{{ timeline.summary }}</p>
          <h4>缺口</h4>
          <ul v-if="gaps.length" class="gap-list">
            <li v-for="(gap, index) in gaps" :key="index">{{ gap }}</li>
          </ul>
          <p v-else>无</p>
        </template>
        <template v-else-if="timelineState === 'pending'">
          <p>分析中</p>
          <el-button type="primary" :loading="timelineLoading" @click="loadTimeline">刷新</el-button>
        </template>
        <template v-else-if="timelineState === 'missing'">
          <p>未能生成</p>
          <p>{{ timelineMessage }}</p>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'

export default {
  name: 'Events',
  setup() {
    const events = ref([])
    const loading = ref(false)
    const page = ref(1)
    const perPage = ref(20)
    const total = ref(0)
    const severityFilter = ref('')
    const drawerVisible = ref(false)
    const timelineLoading = ref(false)
    const timeline = ref(null)
    const timelineState = ref('')
    const timelineMessage = ref('')
    const currentEventId = ref(null)

    const confidenceLabels = {
      high: '高',
      medium: '中',
      low: '低'
    }

    const actor = computed(() => (timeline.value && timeline.value.actor) || {})
    const gaps = computed(() => {
      const list = timeline.value && timeline.value.gaps
      return Array.isArray(list) ? list : []
    })
    const timelineEvents = computed(() => {
      const list = timeline.value && timeline.value.events
      return Array.isArray(list) ? list : []
    })
    const confidenceText = computed(() => {
      const value = timeline.value && timeline.value.confidence
      return confidenceLabels[value] || value || ''
    })
    const actorUrl = computed(() => {
      const method = actor.value.method || ''
      const url = actor.value.url || ''
      const text = [method, url].filter(Boolean).join(' ')
      return text || '未知'
    })

    const displayValue = (value) => value || '未知'

    const applyTimeline = (data) => {
      const body = data && typeof data === 'object' ? data : {}
      timeline.value = body
      timelineMessage.value = body.message || ''
      if (body.status === 'pending') {
        timelineState.value = 'pending'
      } else if (body.status === 'failed') {
        timelineState.value = 'failed'
      } else if (body.status === 'missing') {
        timelineState.value = 'missing'
      } else if (body.status === 'ok') {
        timelineState.value = 'ok'
      } else if (body.summary) {
        timelineState.value = 'failed'
      } else {
        timelineState.value = 'missing'
        timelineMessage.value = body.message || ''
      }
    }

    const loadTimeline = async () => {
      const eventId = currentEventId.value
      if (eventId == null || eventId === '') return
      timelineLoading.value = true
      try {
        const data = await api.get('/events/' + eventId + '/timeline', { skipErrorToast: true })
        if (currentEventId.value !== eventId) return
        applyTimeline(data)
      } catch (error) {
        if (currentEventId.value !== eventId) return
        const status = error.response && error.response.status
        const data = (error.response && error.response.data) || {}
        timeline.value = null
        if (status === 404) {
          timelineState.value = 'missing'
          timelineMessage.value = data.message || ''
        } else if (status === 202 || data.status === 'pending') {
          timelineState.value = 'pending'
          timeline.value = data
        } else {
          timelineState.value = 'missing'
          timelineMessage.value = data.message || data.error || '请求失败'
        }
      } finally {
        if (currentEventId.value === eventId) {
          timelineLoading.value = false
        }
      }
    }

    const canTrack = (row) => (
      !!row
      && row.event_type === 'file_create'
      && !row.incident_id
      && typeof row.file_path === 'string'
      && row.file_path.startsWith('/')
    )

    const startTrack = async (row, quarantine) => {
      if (!canTrack(row)) return
      try {
        const data = await api.post(
          '/events/' + row.id + '/investigate',
          { quarantine },
          { skipErrorToast: true }
        )
        row.incident_id = data.incident_id
        ElMessage.success(quarantine ? '已隔离并开始追踪' : '已开始追踪')
      } catch (error) {
        const data = error.response && error.response.data
        ElMessage.error((data && data.error) || '追踪失败')
      }
    }

    const openTimeline = (row) => {
      if (!row || !row.incident_id) return
      currentEventId.value = row.id
      timeline.value = null
      timelineState.value = ''
      timelineMessage.value = ''
      drawerVisible.value = true
      loadTimeline()
    }

    const rowClassName = ({ row }) => (row && row.incident_id ? 'is-timeline' : '')
    
    const loadEvents = async () => {
      loading.value = true
      try {
        const params = {
          page: page.value,
          per_page: perPage.value
        }
        
        if (severityFilter.value) {
          params.severity = severityFilter.value
        }
        
        const data = await api.get('/events/', { params })
        events.value = data.events
        total.value = data.total
      } catch (error) {
        console.error('加载事件失败', error)
      } finally {
        loading.value = false
      }
    }
    
    const handleSizeChange = (val) => {
      perPage.value = val
      loadEvents()
    }
    
    const handlePageChange = (val) => {
      page.value = val
      loadEvents()
    }
    
    const getSeverityType = (severity) => {
      const map = {
        CRITICAL: 'danger',
        WARNING: 'warning',
        INFO: 'info'
      }
      return map[severity] || ''
    }
    
    watch(severityFilter, () => {
      page.value = 1
      loadEvents()
    })
    
    onMounted(() => {
      loadEvents()
    })
    
    return {
      events,
      loading,
      page,
      perPage,
      total,
      severityFilter,
      handleSizeChange,
      handlePageChange,
      getSeverityType,
      drawerVisible,
      timelineLoading,
      timeline,
      timelineState,
      timelineMessage,
      actor,
      gaps,
      timelineEvents,
      confidenceText,
      actorUrl,
      displayValue,
      openTimeline,
      loadTimeline,
      rowClassName,
      canTrack,
      startTrack
    }
  }
}
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.pagination {
  margin-top: 20px;
  display: flex;
  justify-content: flex-end;
}

.events :deep(.el-table__row.is-timeline) {
  cursor: pointer;
}

.gap-list,
.event-list {
  margin: 0 0 12px;
  padding-left: 18px;
}

.event-meta {
  color: #909399;
  font-size: 12px;
}

h4 {
  margin: 16px 0 8px;
}
</style>

