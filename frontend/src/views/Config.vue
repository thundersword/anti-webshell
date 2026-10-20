<template>
  <div class="config">
    <el-alert
      v-if="!currentSiteId"
      type="warning"
      title="请先在「站点管理」中创建并选择站点"
      :closable="false"
      style="margin-bottom: 16px"
    />
    <el-alert
      v-else
      type="info"
      :title="`当前站点: ${currentSiteId}`"
      :closable="false"
      style="margin-bottom: 16px"
    />
    <el-tabs v-model="activeTab">
      <el-tab-pane label="关键文件" name="critical">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>关键文件管理</span>
              <el-button type="primary" @click="handleAddCriticalFile">
                添加文件
              </el-button>
            </div>
          </template>
          
          <el-table :data="criticalFiles" style="width: 100%">
            <el-table-column prop="file_path" label="文件路径" />
            <el-table-column prop="enabled" label="状态">
              <template #default="{ row }">
                <el-tag :type="row.enabled ? 'success' : 'info'">
                  {{ row.enabled ? '启用' : '禁用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="150">
              <template #default="{ row }">
                <el-button type="danger" size="small" @click="handleDeleteCriticalFile(row)">
                  删除
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
      
      <el-tab-pane label="白名单目录" name="whitelist">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>白名单目录管理</span>
              <el-button type="primary" @click="handleAddWhitelistDir">
                添加目录
              </el-button>
            </div>
          </template>
          
          <el-table :data="whitelistDirs" style="width: 100%">
            <el-table-column prop="dir_path" label="目录路径" />
            <el-table-column prop="enabled" label="状态">
              <template #default="{ row }">
                <el-tag :type="row.enabled ? 'success' : 'info'">
                  {{ row.enabled ? '启用' : '禁用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="150">
              <template #default="{ row }">
                <el-button type="danger" size="small" @click="handleDeleteWhitelistDir(row)">
                  删除
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
      
      <el-tab-pane label="告警配置" name="alert">
        <el-card>
          <template #header>
            <span>告警配置</span>
          </template>
          
          <el-table :data="alertConfigs" style="width: 100%">
            <el-table-column prop="type" label="告警类型">
              <template #default="{ row }">
                <el-tag>{{ getAlertTypeName(row.type) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="enabled" label="启用状态">
              <template #default="{ row }">
                <el-switch
                  v-model="row.enabled"
                  @change="handleToggleAlert(row)"
                />
              </template>
            </el-table-column>
            <el-table-column prop="webhook_url" label="Webhook URL" />
            <el-table-column prop="alert_email" label="告警邮箱" />
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script>
import { ref, computed, onMounted, watch } from 'vue'
import api from '../api/index.js'
import { useSiteStore } from '../store/site.js'
import { ElMessage, ElMessageBox } from 'element-plus'

export default {
  name: 'Config',
  setup() {
    const siteStore = useSiteStore()
    const currentSiteId = computed(() => siteStore.currentSiteId)
    const activeTab = ref('critical')
    const criticalFiles = ref([])
    const whitelistDirs = ref([])
    const alertConfigs = ref([])
    
    const loadCriticalFiles = async () => {
      try {
        const data = await api.get('/config/critical-files')
        criticalFiles.value = data
      } catch (error) {
        ElMessage.error('加载关键文件失败')
      }
    }
    
    const loadWhitelistDirs = async () => {
      try {
        const data = await api.get('/config/whitelist-dirs')
        whitelistDirs.value = data
      } catch (error) {
        ElMessage.error('加载白名单目录失败')
      }
    }
    
    const loadAlertConfigs = async () => {
      try {
        const data = await api.get('/config/alert-config')
        alertConfigs.value = data
      } catch (error) {
        ElMessage.error('加载告警配置失败')
      }
    }
    
    const handleAddCriticalFile = () => {
      ElMessageBox.prompt('请输入文件路径', '添加关键文件', {
        confirmButtonText: '确定',
        cancelButtonText: '取消'
      }).then(async ({ value }) => {
        await api.post('/config/critical-files', { file_path: value })
        ElMessage.success('添加成功')
        loadCriticalFiles()
      })
    }
    
    const handleDeleteCriticalFile = async (row) => {
      await ElMessageBox.confirm('确定删除该文件？', '提示', {
        type: 'warning'
      })
      
      await api.delete(`/config/critical-files/${row.id}`)
      ElMessage.success('删除成功')
      loadCriticalFiles()
    }
    
    const handleAddWhitelistDir = () => {
      ElMessageBox.prompt('请输入目录路径', '添加白名单目录', {
        confirmButtonText: '确定',
        cancelButtonText: '取消'
      }).then(async ({ value }) => {
        await api.post('/config/whitelist-dirs', { dir_path: value })
        ElMessage.success('添加成功')
        loadWhitelistDirs()
      })
    }
    
    const handleDeleteWhitelistDir = async (row) => {
      await ElMessageBox.confirm('确定删除该目录？', '提示', {
        type: 'warning'
      })
      
      await api.delete(`/config/whitelist-dirs/${row.id}`)
      ElMessage.success('删除成功')
      loadWhitelistDirs()
    }
    
    const handleToggleAlert = async (row) => {
      try {
        await api.put(`/config/alert-config/${row.id}`, { enabled: row.enabled })
        ElMessage.success('更新成功')
      } catch (error) {
        ElMessage.error('更新失败')
      }
    }
    
    const getAlertTypeName = (type) => {
      const map = {
        email: '邮件',
        wechat: '企业微信',
        dingtalk: '钉钉'
      }
      return map[type] || type
    }
    
    const reloadAll = () => {
      if (!currentSiteId.value) return
      loadCriticalFiles()
      loadWhitelistDirs()
      loadAlertConfigs()
    }

    watch(currentSiteId, reloadAll)

    onMounted(reloadAll)
    
    return {
      currentSiteId,
      activeTab,
      criticalFiles,
      whitelistDirs,
      alertConfigs,
      handleAddCriticalFile,
      handleDeleteCriticalFile,
      handleAddWhitelistDir,
      handleDeleteWhitelistDir,
      handleToggleAlert,
      getAlertTypeName
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
</style>

