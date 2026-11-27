<template>
  <div class="config">
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
      
      <el-tab-pane label="防护策略" name="protect">
        <el-card>
          <el-form label-width="160px" style="max-width: 640px">
            <el-form-item label="自动恢复">
              <el-switch v-model="protect.auto_restore" @change="saveProtect" />
              <span class="hint">关键文件被改后从备份写回</span>
            </el-form-item>
            <el-form-item label="锁定关键文件">
              <el-switch v-model="protect.lock_critical_files" @change="saveProtect" />
              <span class="hint">chattr +i，Web 进程无法改写（发版前请先解锁）</span>
            </el-form-item>
            <el-form-item label="访问日志">
              <el-input v-model="protect.access_log" :disabled="!protectLoaded" />
            </el-form-item>
            <el-form-item label="Host">
              <el-input
                v-model="protect.site_hosts"
                :disabled="!protectLoaded"
                placeholder="blog.example www.blog.example"
              />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :disabled="!protectLoaded" @click="saveProtect">保存</el-button>
            </el-form-item>
            <el-form-item label="立即操作">
              <el-button type="primary" :loading="lockBusy" @click="doLock(true)">立即锁定</el-button>
              <el-button :loading="lockBusy" @click="doLock(false)">发版解锁</el-button>
            </el-form-item>
          </el-form>
          <pre class="lock-status">{{ lockStatusText || '尚未查询锁定状态' }}</pre>
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
    const protect = ref({
      auto_restore: true,
      lock_critical_files: true,
      access_log: '',
      site_hosts: ''
    })
    const protectLoaded = ref(false)
    const lockBusy = ref(false)
    const lockStatusText = ref('')
    
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
    
    const loadProtect = async () => {
      if (!currentSiteId.value) return
      protectLoaded.value = false
      protect.value.access_log = ''
      protect.value.site_hosts = ''
      try {
        const data = await api.get(`/sites/${currentSiteId.value}`)
        protect.value.auto_restore = !!data.auto_restore
        protect.value.lock_critical_files = data.lock_critical_files !== false
        protect.value.access_log = data.access_log || ''
        protect.value.site_hosts = data.site_hosts || ''
        protectLoaded.value = true
        const st = await api.get('/service/lock-status')
        lockStatusText.value = st.status || ''
      } catch (error) {
        // ignore
      }
    }

    const saveProtect = async () => {
      if (!currentSiteId.value) return
      try {
        const payload = {
          auto_restore: protect.value.auto_restore,
          lock_critical_files: protect.value.lock_critical_files
        }
        if (protectLoaded.value) {
          payload.access_log = protect.value.access_log
          payload.site_hosts = protect.value.site_hosts
        }
        const saved = await api.put(`/sites/${currentSiteId.value}`, payload)
        if (saved && saved.sync_ok === false) {
          ElMessage.error(saved.sync_message || '站点配置文件未更新')
          return
        }
        ElMessage.success('防护策略已保存')
        loadProtect()
      } catch (error) {
        ElMessage.error('保存失败')
      }
    }

    const doLock = async (lock) => {
      lockBusy.value = true
      try {
        await api.post(lock ? '/service/lock' : '/service/unlock')
        ElMessage.success(lock ? '已锁定关键文件' : '已解锁，可以发版')
        loadProtect()
      } catch (error) {
        ElMessage.error(lock ? '锁定失败' : '解锁失败')
      } finally {
        lockBusy.value = false
      }
    }
    
    const reloadSiteScoped = () => {
      if (!currentSiteId.value) return
      loadCriticalFiles()
      loadWhitelistDirs()
      loadProtect()
    }

    watch(currentSiteId, reloadSiteScoped)

    onMounted(() => {
      reloadSiteScoped()
    })
    
    return {
      activeTab,
      criticalFiles,
      whitelistDirs,
      handleAddCriticalFile,
      handleDeleteCriticalFile,
      handleAddWhitelistDir,
      handleDeleteWhitelistDir,
      protect,
      protectLoaded,
      lockBusy,
      lockStatusText,
      saveProtect,
      doLock
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

.hint {
  margin-left: 12px;
  color: #909399;
  font-size: 13px;
}

.lock-status {
  margin-top: 12px;
  padding: 12px;
  background: #1e1e1e;
  color: #d4d4d4;
  font-size: 12px;
  white-space: pre-wrap;
}
</style>