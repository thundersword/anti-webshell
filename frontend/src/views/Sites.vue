<template>
  <div class="sites">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>站点管理</span>
          <el-button type="primary" @click="openCreate">添加站点</el-button>
        </div>
      </template>

      <el-table :data="sites" v-loading="loading">
        <el-table-column prop="site_id" label="站点 ID" width="140" />
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="profile" label="框架" width="120" />
        <el-table-column prop="project_dir" label="目录" show-overflow-tooltip />
        <el-table-column prop="node_id" label="节点" width="100" />
        <el-table-column prop="sync_status" label="同步" width="100">
          <template #default="{ row }">
            <el-tag :type="row.sync_status === 'synced' ? 'success' : 'warning'" size="small">
              {{ row.sync_status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220">
          <template #default="{ row }">
            <el-button size="small" @click="selectSite(row)">切换</el-button>
            <el-button size="small" type="danger" @click="removeSite(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" title="添加站点" width="560px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="站点 ID" required>
          <el-input v-model="form.site_id" placeholder="字母数字，如 blog" />
        </el-form-item>
        <el-form-item label="显示名称">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="项目目录" required>
          <el-input v-model="form.project_dir" placeholder="/www/wwwroot/blog">
            <template #append>
              <el-button :loading="detecting" @click="detectFramework">探测框架</el-button>
            </template>
          </el-input>
        </el-form-item>
        <el-form-item label="框架 Profile">
          <el-select v-model="form.profile" style="width: 100%" filterable>
            <el-option
              v-for="p in profiles"
              :key="p.name"
              :label="p.label || p.name"
              :value="p.name"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="告警邮箱">
          <el-input v-model="form.alert_email" />
        </el-form-item>
        <el-form-item label="锁定关键文件">
          <el-switch v-model="form.lock_critical_files" />
        </el-form-item>
        <el-form-item label="创建后启动">
          <el-switch v-model="form.start" />
        </el-form-item>
        <el-alert
          v-if="profileHint"
          type="info"
          :closable="false"
          :title="profileHint"
          style="margin-bottom: 12px"
        />
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="createSite">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script>
import { ref, onMounted, computed } from 'vue'
import api from '../api/index.js'
import { useSiteStore } from '../store/site.js'
import { ElMessage, ElMessageBox } from 'element-plus'

export default {
  name: 'Sites',
  setup() {
    const siteStore = useSiteStore()
    const loading = ref(false)
    const saving = ref(false)
    const detecting = ref(false)
    const dialogVisible = ref(false)
    const form = ref({
      site_id: '',
      name: '',
      project_dir: '',
      profile: 'thinkphp5',
      alert_email: '',
      lock_critical_files: true,
      start: true
    })

    const sites = computed(() => siteStore.sites)
    const profiles = computed(() => siteStore.profiles)

    const profileHint = computed(() => {
      const p = profiles.value.find((x) => x.name === form.value.profile)
      if (!p) return ''
      return `${p.label || p.name}：预填关键文件 ${p.critical_files.length} 个、白名单 ${p.whitelist_dirs.length} 个；将排除 .git / vendor / runtime 等热目录`
    })

    const loadSites = async () => {
      loading.value = true
      try {
        const data = await api.get('/sites')
        siteStore.setSites(data)
      } finally {
        loading.value = false
      }
    }

    const loadProfiles = async () => {
      const data = await api.get('/profiles')
      siteStore.setProfiles(data.profiles || [])
    }

    const openCreate = () => {
      form.value = {
        site_id: '',
        name: '',
        project_dir: '',
        profile: 'thinkphp5',
        alert_email: '',
        lock_critical_files: true,
        start: true
      }
      dialogVisible.value = true
    }

    const detectFramework = async () => {
      if (!form.value.project_dir) {
        ElMessage.warning('请先填写项目目录')
        return
      }
      detecting.value = true
      try {
        const data = await api.get('/profiles/detect', {
          params: { project_dir: form.value.project_dir }
        })
        if (data.profile) {
          form.value.profile = data.profile
        }
        ElMessage.success(`${data.display_name || data.profile}（${data.confidence}）：${data.reason}`)
      } finally {
        detecting.value = false
      }
    }

    const createSite = async () => {
      if (!form.value.site_id || !form.value.project_dir) {
        ElMessage.warning('请填写站点 ID 与项目目录')
        return
      }
      saving.value = true
      try {
        await api.post('/sites', {
          ...form.value,
          name: form.value.name || form.value.site_id
        })
        ElMessage.success('站点已创建')
        dialogVisible.value = false
        await loadSites()
        siteStore.setCurrentSiteId(form.value.site_id)
      } catch (e) {
        // interceptor already toasts
      } finally {
        saving.value = false
      }
    }

    const selectSite = (row) => {
      siteStore.setCurrentSiteId(row.site_id)
      ElMessage.success(`已切换到站点 ${row.site_id}`)
    }

    const removeSite = async (row) => {
      await ElMessageBox.confirm(`确定删除站点 ${row.site_id}？`, '提示', { type: 'warning' })
      await api.delete(`/sites/${row.site_id}`)
      ElMessage.success('已删除')
      await loadSites()
    }

    onMounted(async () => {
      await loadProfiles()
      await loadSites()
    })

    return {
      sites,
      profiles,
      loading,
      saving,
      dialogVisible,
      form,
      profileHint,
      detecting,
      openCreate,
      detectFramework,
      createSite,
      selectSite,
      removeSite
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
