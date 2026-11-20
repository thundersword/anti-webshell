<template>
  <div class="alerts">
    <el-card>
      <template #header>
        <span>告警配置</span>
      </template>

      <el-alert
        type="info"
        :closable="false"
        title="全局告警：对所有站点与控制面生效"
        style="margin-bottom: 12px"
      />

      <el-table :data="alertConfigs" style="width: 100%">
        <el-table-column prop="type" label="告警类型">
          <template #default="{ row }">
            <el-tag>{{ getAlertTypeName(row.type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="enabled" label="启用状态">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" />
          </template>
        </el-table-column>
        <el-table-column label="Webhook / 邮箱">
          <template #default="{ row }">
            <el-input
              v-if="row.type === 'email'"
              v-model="row.alert_email"
              placeholder="alert@example.com"
            />
            <el-input
              v-else
              v-model="row.webhook_url"
              placeholder="https://..."
            />
          </template>
        </el-table-column>
      </el-table>
      <el-button
        type="primary"
        :loading="alertSaving"
        style="margin-top: 12px"
        @click="saveAlerts"
      >
        保存告警配置
      </el-button>
    </el-card>
  </div>
</template>

<script>
import { ref, onMounted } from 'vue'
import api from '../api/index.js'
import { ElMessage } from 'element-plus'

export default {
  name: 'Alerts',
  setup() {
    const alertConfigs = ref([])
    const alertSaving = ref(false)

    const loadAlertConfigs = async () => {
      try {
        const data = await api.get('/config/alert-config')
        alertConfigs.value = data
      } catch (error) {
        ElMessage.error('加载告警配置失败')
      }
    }

    const saveAlerts = async () => {
      alertSaving.value = true
      try {
        const data = await api.put('/config/alert-config', {
          channels: alertConfigs.value.map((row) => ({
            id: row.id,
            enabled: row.enabled,
            webhook_url: row.webhook_url,
            alert_email: row.alert_email
          }))
        })
        alertConfigs.value = data.channels || []
        ElMessage.success('告警配置已保存')
      } catch (error) {
        ElMessage.error('保存告警配置失败')
      } finally {
        alertSaving.value = false
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

    onMounted(() => {
      loadAlertConfigs()
    })

    return {
      alertConfigs,
      alertSaving,
      saveAlerts,
      getAlertTypeName
    }
  }
}
</script>