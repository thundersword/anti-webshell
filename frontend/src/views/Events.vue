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
      
      <el-table :data="events" style="width: 100%" v-loading="loading">
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
  </div>
</template>

<script>
import { ref, watch, onMounted } from 'vue'
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
      getSeverityType
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
</style>

