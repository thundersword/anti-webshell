<template>
  <div class="site-workspace">
    <el-page-header @back="$router.push('/sites')" :content="pageTitle" />
    <el-tabs :model-value="activeTab" @tab-change="onTab">
      <el-tab-pane label="概览" name="overview" />
      <el-tab-pane label="配置" name="config" />
      <el-tab-pane label="监控" name="monitor" />
      <el-tab-pane label="事件" name="events" />
    </el-tabs>
    <router-view />
  </div>
</template>

<script>
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSiteStore } from '../store/site.js'
import api from '../api/index.js'

export default {
  name: 'SiteWorkspace',
  props: {
    siteId: {
      type: String,
      required: true
    }
  },
  setup(props) {
    const route = useRoute()
    const router = useRouter()
    const siteStore = useSiteStore()

    const pageTitle = computed(() => {
      const site = siteStore.sites.find((s) => s.site_id === props.siteId)
      return site?.name ? `站点 ${site.name}` : `站点 ${props.siteId}`
    })

    const activeTab = computed(() => {
      const name = route.name
      if (name === 'SiteConfig') return 'config'
      if (name === 'SiteMonitor') return 'monitor'
      if (name === 'SiteEvents') return 'events'
      return 'overview'
    })

    const tabRoutes = {
      overview: () => `/sites/${props.siteId}`,
      config: () => `/sites/${props.siteId}/config`,
      monitor: () => `/sites/${props.siteId}/monitor`,
      events: () => `/sites/${props.siteId}/events`
    }

    const onTab = (tab) => {
      const to = tabRoutes[tab]?.() ?? tabRoutes.overview()
      if (route.path !== to) {
        router.push(to)
      }
    }

    const verifySite = async () => {
      try {
        const data = await api.get('/sites')
        siteStore.setSites(data)
        const exists = data.some((s) => s.site_id === props.siteId)
        if (!exists) {
          router.replace('/sites')
          return
        }
        siteStore.setCurrentSiteId(props.siteId)
      } catch {
        router.replace('/sites')
      }
    }

    watch(
      () => route.params.siteId,
      (id) => {
        if (id) {
          siteStore.setCurrentSiteId(id)
        }
      },
      { immediate: true }
    )

    onMounted(verifySite)

    return {
      pageTitle,
      activeTab,
      onTab
    }
  }
}
</script>

<style scoped>
.site-workspace {
  min-height: 200px;
}
</style>