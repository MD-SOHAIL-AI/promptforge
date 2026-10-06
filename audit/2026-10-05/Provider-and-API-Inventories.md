# Provider and API Inventories

This is descriptor/route evidence, not a list of successfully exercised external accounts. All default providers dispatch through Python OpenCodeProvider → Node engine. Ten named provider definitions have explicit API-key/local semantics; remaining descriptors derive from pinned Models.dev data. Unknown/special SDK compatibility and remote entitlements remain unverified. For every descriptor, retry/timeouts/usage/error normalization/admission use the common pipeline in sections 8–11 of the main report; default circuit enforcement is bypassed. Tool capability is per-model metadata, not a provider-wide guarantee.

Auth below is registry metadata. AWS Bedrock uses ambient/profile credential chains when no explicit key; Vertex uses Google Cloud ADC/project/service-account configuration. Neither is consumer subscription OAuth. GitHub Copilot uses a supplied bearer token, without a full account-login/refresh flow. Custom providers are user-created at runtime and therefore not a finite checked-in ID list.

## All provider descriptors

| ID | Registry auth | Environment names | Base endpoint | Catalog SDK | Catalog model count | Implementation / verification |
|---|---|---|---|---|---:|---|
| deepseek | api_key | DEEPSEEK_API_KEY | https://api.deepseek.com | @ai-sdk/openai-compatible | 4 | Explicit named definition; shared engine dispatch; account/live UNVERIFIED |
| anthropic | api_key | ANTHROPIC_API_KEY | https://api.anthropic.com/v1 | @ai-sdk/anthropic | 16 | Explicit named definition; shared engine dispatch; account/live UNVERIFIED |
| openrouter | api_key | OPENROUTER_API_KEY | https://openrouter.ai/api/v1 | @openrouter/ai-sdk-provider | 390 | Explicit named definition; shared engine dispatch; account/live UNVERIFIED |
| openai | api_key | OPENAI_API_KEY | https://api.openai.com/v1 | @ai-sdk/openai | 53 | Explicit named definition; shared engine dispatch; account/live UNVERIFIED |
| groq | api_key | GROQ_API_KEY | https://api.groq.com/openai/v1 | @ai-sdk/groq | 16 | Explicit named definition; shared engine dispatch; account/live UNVERIFIED |
| cerebras | api_key | CEREBRAS_API_KEY | https://api.cerebras.ai/v1 | @ai-sdk/cerebras | 2 | Explicit named definition; shared engine dispatch; account/live UNVERIFIED |
| nvidia_nim | api_key | NVIDIA_API_KEY | https://integrate.api.nvidia.com/v1 | @ai-sdk/openai-compatible | 106 | Explicit named definition; shared engine dispatch; account/live UNVERIFIED |
| gemini | api_key | GEMINI_API_KEY, GOOGLE_API_KEY, GOOGLE_GENERATIVE_AI_API_KEY | https://generativelanguage.googleapis.com/v1beta | @ai-sdk/google | 39 | Explicit named definition; shared engine dispatch; account/live UNVERIFIED |
| lmstudio | none | LMSTUDIO_API_KEY | http://localhost:1234/v1 | @ai-sdk/openai-compatible | 3 | Explicit named definition; shared engine dispatch; account/live UNVERIFIED |
| ollama | none | none / options | http://localhost:11434 | SDK/default/options | 0 | Explicit named definition; shared engine dispatch; account/live UNVERIFIED |
| deepinfra | api_key | DEEPINFRA_API_KEY | SDK/default/options | @ai-sdk/deepinfra | 71 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| perplexity-agent | api_key | PERPLEXITY_API_KEY | https://api.perplexity.ai/v1 | @ai-sdk/openai | 22 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| bailing | api_key | BAILING_API_TOKEN | https://api.tbox.cn/api/llm/v1 | @ai-sdk/openai-compatible | 2 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| poe | api_key | POE_API_KEY | https://api.poe.com/v1 | @ai-sdk/openai-compatible | 137 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| blueclaw | api_key | BLUECLAW_API_KEY | https://openai.blueclaw.network/v1 | @ai-sdk/openai-compatible | 2 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| zai | api_key | ZHIPU_API_KEY | https://api.z.ai/api/paas/v4 | @ai-sdk/openai-compatible | 18 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| empiriolabs | api_key | EMPIRIOLABS_API_KEY | https://api.empiriolabs.ai/v1 | @ai-sdk/openai-compatible | 66 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| sensenova | api_key | SENSENOVA_API_KEY | https://token.sensenova.cn/v1 | @ai-sdk/openai-compatible | 5 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| alibaba-token-plan | api_key | ALIBABA_TOKEN_PLAN_API_KEY | https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1 | @ai-sdk/openai-compatible | 28 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| cloudflare-workers-ai | api_key | CLOUDFLARE_API_KEY, CLOUDFLARE_ACCOUNT_ID | https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/ai/v1 | @ai-sdk/openai-compatible | 27 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| engy | api_key | ENGY_API_KEY | https://api.engy.ai/v1 | @ai-sdk/openai-compatible | 8 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| poolside | api_key | POOLSIDE_API_KEY | https://inference.poolside.ai/v1 | @ai-sdk/openai-compatible | 3 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| nano-gpt | api_key | NANO_GPT_API_KEY | https://nano-gpt.com/api/v1 | @ai-sdk/openai-compatible | 600 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| abliteration-ai | api_key | ABLIT_KEY | https://api.abliteration.ai/v1 | @ai-sdk/openai-compatible | 3 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| crof | api_key | CROF_API_KEY | https://crof.ai/v1 | @ai-sdk/openai-compatible | 24 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| standardcompute | api_key | STANDARDCOMPUTE_API_KEY | https://api.stdcmpt.com/v1 | @openrouter/ai-sdk-provider | 1 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| cloudferro-sherlock | api_key | CLOUDFERRO_SHERLOCK_API_KEY | https://api-sherlock.cloudferro.com/openai/v1/ | @ai-sdk/openai-compatible | 5 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| tinfoil | api_key | TINFOIL_API_KEY | https://inference.tinfoil.sh/v1 | @ai-sdk/openai-compatible | 9 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| llama | api_key | LLAMA_API_KEY | https://api.llama.com/compat/v1/ | @ai-sdk/openai-compatible | 7 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| cohere | api_key | COHERE_API_KEY | SDK/default/options | @ai-sdk/cohere | 17 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| baseten | api_key | BASETEN_API_KEY | https://inference.baseten.co/v1 | @ai-sdk/openai-compatible | 24 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| nan | api_key | NAN_API_KEY | https://api.nan.builders/v1 | @ai-sdk/openai-compatible | 8 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| stepfun-ai-step-plan | api_key | STEPFUN_API_KEY | https://api.stepfun.ai/step_plan/v1 | @ai-sdk/openai-compatible | 4 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| nearai | api_key | NEARAI_API_KEY | https://cloud-api.near.ai/v1 | @ai-sdk/openai-compatible | 32 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| wandb | api_key | WANDB_API_KEY | https://api.inference.wandb.ai/v1 | @ai-sdk/openai-compatible | 29 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| subconscious | api_key | SUBCONSCIOUS_API_KEY | https://api.subconscious.dev/v1 | @ai-sdk/anthropic | 2 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| zeldoc | api_key | ZELDOC_API_KEY | https://api.zeldoc.ai/v1 | @ai-sdk/openai-compatible | 1 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| databricks | api_key | DATABRICKS_TOKEN, DATABRICKS_HOST | https://${DATABRICKS_HOST}/ai-gateway/mlflow/v1 | @ai-sdk/openai-compatible | 30 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| umans-ai-coding-plan | api_key | UMANS_AI_CODING_PLAN_API_KEY | https://api.code.umans.ai/v1 | @ai-sdk/openai-compatible | 8 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| kuae-cloud-coding-plan | api_key | KUAE_API_KEY | https://coding-plan-endpoint.kuaecloud.net/v1 | @ai-sdk/openai-compatible | 1 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| umans-ai | api_key | UMANS_AI_API_KEY | https://api.code.umans.ai/v1 | @ai-sdk/openai-compatible | 7 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| siliconflow | api_key | SILICONFLOW_API_KEY | https://api.siliconflow.com/v1 | @ai-sdk/openai-compatible | 57 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| minimax-coding-plan | api_key | MINIMAX_API_KEY | https://api.minimax.io/anthropic/v1 | @ai-sdk/anthropic | 8 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| alibaba-coding-plan | api_key | ALIBABA_CODING_PLAN_API_KEY | https://coding-intl.dashscope.aliyuncs.com/v1 | @ai-sdk/openai-compatible | 10 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| lilac | api_key | LILAC_API_KEY | https://api.getlilac.com/v1 | @ai-sdk/openai-compatible | 4 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| moonshotai-cn | api_key | MOONSHOT_API_KEY | https://api.moonshot.cn/v1 | @ai-sdk/openai-compatible | 4 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| bothub | api_key | BOTHUB_API_KEY | https://openai.bothub.ru/v1 | @ai-sdk/openai-compatible | 8 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| xiaomi-token-plan-cn | api_key | XIAOMI_API_KEY | https://token-plan-cn.xiaomimimo.com/v1 | @ai-sdk/openai-compatible | 9 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| privatemode-ai | api_key | PRIVATEMODE_API_KEY, PRIVATEMODE_ENDPOINT | http://localhost:8080/v1 | @ai-sdk/openai-compatible | 11 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| llmgateway-providers | api_key | LLMGATEWAY_API_KEY | https://api.llmgateway.io/v1 | @ai-sdk/openai-compatible | 438 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| above | api_key | ABOVE_API_KEY | https://api.above.dev/v1 | @ai-sdk/openai-compatible | 10 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| kilo | api_key | KILO_API_KEY | https://api.kilo.ai/api/gateway | @ai-sdk/openai-compatible | 397 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| venice | api_key | VENICE_API_KEY | SDK/default/options | venice-ai-sdk-provider | 116 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| alibaba-token-plan-cn | api_key | ALIBABA_TOKEN_PLAN_API_KEY | https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1 | @ai-sdk/openai-compatible | 28 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| ai21 | api_key | AI21_API_KEY | https://api.ai21.com/studio/v1 | @ai-sdk/openai-compatible | 2 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| inference | api_key | INFERENCE_API_KEY | https://inference.net/v1 | @ai-sdk/openai-compatible | 9 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| iflowcn | api_key | IFLOW_API_KEY | https://apis.iflow.cn/v1 | @ai-sdk/openai-compatible | 14 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| minimax-cn-coding-plan | api_key | MINIMAX_API_KEY | https://api.minimax.cn/anthropic/v1 | @ai-sdk/anthropic | 8 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| thinkingmachines | api_key | TINKER_API_KEY | https://tinker.thinkingmachines.dev/services/tinker-prod/anthropic/api/v1 | @ai-sdk/anthropic | 2 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| stepfun-step-plan | api_key | STEPFUN_API_KEY | https://api.stepfun.com/step_plan/v1 | @ai-sdk/openai-compatible | 5 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| melious | api_key | MELIOUS_API_KEY | https://api.melious.ai/v1 | @ai-sdk/openai-compatible | 12 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| berget | api_key | BERGET_API_KEY | https://api.berget.ai/v1 | @ai-sdk/openai-compatible | 6 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| snowflake-cortex | api_key | SNOWFLAKE_CORTEX_PAT, SNOWFLAKE_ACCOUNT | https://${SNOWFLAKE_ACCOUNT}.snowflakecomputing.com/api/v2/cortex/v1 | @ai-sdk/openai-compatible | 25 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| sarvam | api_key | SARVAM_API_KEY | https://api.sarvam.ai/v1 | @ai-sdk/openai-compatible | 2 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| nova | api_key | NOVA_API_KEY | https://api.nova.amazon.com/v1 | @ai-sdk/openai-compatible | 2 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| abacus | api_key | ABACUS_API_KEY | https://routellm.abacus.ai/v1 | @ai-sdk/openai-compatible | 108 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| novita-ai | api_key | NOVITA_API_KEY | https://api.novita.ai/openai | @ai-sdk/openai-compatible | 107 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| 302ai | api_key | 302AI_API_KEY | https://api.302.ai/v1 | @ai-sdk/openai-compatible | 122 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| perplexity | api_key | PERPLEXITY_API_KEY | SDK/default/options | @ai-sdk/perplexity | 4 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| iteracompute | api_key | ITERACOMPUTE_API_KEY | https://api.iteracompute.com/v1 | @ai-sdk/openai-compatible | 9 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| the-grid-ai | api_key | THEGRID_API_KEY | https://api.thegrid.ai/v1 | @ai-sdk/openai-compatible | 9 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| meta | api_key | META_MODEL_API_KEY | https://api.meta.ai/v1 | @ai-sdk/openai | 5 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| cline-pass | api_key | CLINE_API_KEY | https://api.cline.bot/api/v1 | @ai-sdk/openai-compatible | 18 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| modal | api_key | MODAL_PROXY_TOKEN | https://inference.us-west.modal.direct/v1 | @ai-sdk/openai-compatible | 4 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| coralbricks | api_key | CORAL_API_KEY | https://inference.coralbricks.ai/v1 | @ai-sdk/openai-compatible | 3 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| routing-run | api_key | ROUTING_RUN_API_KEY | https://api.routing.run/v1 | @ai-sdk/openai-compatible | 15 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| echo | api_key | ECHO_API_KEY | https://echo.tracerml.ai/v1 | @ai-sdk/openai-compatible | 1 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| neuralwatt | api_key | NEURALWATT_API_KEY | https://api.neuralwatt.com/v1 | @ai-sdk/openai-compatible | 29 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| gitlab | api_key | GITLAB_TOKEN | SDK/default/options | gitlab-ai-provider | 30 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| infer | api_key | INFER_API_KEY | https://infer.flow7.org/v1 | @ai-sdk/openai | 2 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| azure | api_key | AZURE_API_KEY, AZURE_RESOURCE_NAME | SDK/default/options | @ai-sdk/azure | 94 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| freemodel | api_key | FREEMODEL_API_KEY | https://cc.freemodel.dev/v1 | @ai-sdk/anthropic | 10 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| azure-cognitive-services | api_key | AZURE_COGNITIVE_SERVICES_API_KEY, AZURE_COGNITIVE_SERVICES_RESOURCE_NAME | SDK/default/options | @ai-sdk/azure | 86 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| pendra | api_key | PENDRA_API_KEY | https://api.pendra.ai/api/v1 | @ai-sdk/openai-compatible | 6 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| moark | api_key | MOARK_API_KEY | https://moark.com/v1 | @ai-sdk/openai-compatible | 2 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| atomic-chat | api_key | ATOMIC_CHAT_API_KEY | http://127.0.0.1:1337/v1 | @ai-sdk/openai-compatible | 5 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| qihang-ai | api_key | QIHANG_API_KEY | https://api.qhaigc.net/v1 | @ai-sdk/openai-compatible | 9 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| ai-router | api_key | AI_ROUTER_API_KEY | https://api.ai-router.dev/v1 | @ai-sdk/openai-compatible | 5 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| llmtr | api_key | LLMTR_API_KEY | https://llmtr.com/v1 | @ai-sdk/openai-compatible | 32 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| alibaba | api_key | DASHSCOPE_API_KEY | https://dashscope-intl.aliyuncs.com/compatible-mode/v1 | @ai-sdk/openai-compatible | 59 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| auriko | api_key | AURIKO_API_KEY | https://api.auriko.ai/v1 | @ai-sdk/openai-compatible | 15 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| zenmux | api_key | ZENMUX_API_KEY | https://zenmux.ai/api/v1 | @ai-sdk/openai-compatible | 129 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| unorouter | api_key | UNOROUTER_API_KEY | https://api.unorouter.com/v1 | @ai-sdk/openai-compatible | 23 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| salad-cloud | api_key | SALAD_CLOUD_API_KEY | SDK/default/options | @saladtechnologies-oss/ai-sdk-provider | 1 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| vispark | api_key | VISPARK_LAB_API_KEY | https://api.lab.vispark.in/v1 | @ai-sdk/openai-compatible | 3 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| siliconflow-cn | api_key | SILICONFLOW_CN_API_KEY | https://api.siliconflow.cn/v1 | @ai-sdk/openai-compatible | 44 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| regolo-ai | api_key | REGOLO_API_KEY | https://api.regolo.ai/v1 | @ai-sdk/openai-compatible | 18 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| xiaomi-token-plan-ams | api_key | XIAOMI_API_KEY | https://token-plan-ams.xiaomimimo.com/v1 | @ai-sdk/openai-compatible | 9 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| inceptron | api_key | INCEPTRON_API_KEY | https://api.inceptron.io/v1 | @ai-sdk/openai-compatible | 4 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| upstage | api_key | UPSTAGE_API_KEY | https://api.upstage.ai/v1/solar | @ai-sdk/openai-compatible | 4 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| vultr | api_key | VULTR_API_KEY | https://api.vultrinference.com/v1 | @ai-sdk/openai-compatible | 15 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| huggingface | api_key | HF_TOKEN | https://router.huggingface.co/v1 | @ai-sdk/openai-compatible | 78 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| volcengine | api_key | ARK_API_KEY | https://ark.cn-beijing.volces.com/api/v3 | @ai-sdk/openai-compatible | 16 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| impossibl | api_key | IMPOSSIBL_API_KEY | https://api.impossibl.com/v1 | @ai-sdk/openai-compatible | 76 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| xpersona | api_key | XPERSONA_API_KEY | https://www.xpersona.co/v1 | @ai-sdk/openai-compatible | 13 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| qiniu-ai | api_key | QINIU_API_KEY | https://api.qnaigc.com/v1 | @ai-sdk/openai-compatible | 91 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| modelscope | api_key | MODELSCOPE_API_KEY | https://api-inference.modelscope.cn/v1 | @ai-sdk/openai-compatible | 7 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| google | api_key | GOOGLE_API_KEY, GOOGLE_GENERATIVE_AI_API_KEY, GEMINI_API_KEY | SDK/default/options | @ai-sdk/google | 39 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| vancine | api_key | VANCINE_API_KEY | https://vancine.com/v1 | @ai-sdk/openai-compatible | 8 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| zhipuai-coding-plan | api_key | ZHIPU_API_KEY | https://open.bigmodel.cn/api/coding/paas/v4 | @ai-sdk/openai-compatible | 4 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| lucidquery | api_key | LUCIDQUERY_API_KEY | https://api.lucidquery.com/v1 | @ai-sdk/openai-compatible | 4 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| gmicloud | api_key | GMICLOUD_API_KEY | https://api.gmi-serving.com/v1 | @ai-sdk/openai-compatible | 17 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| oci | api_key | OCI_GENAI_API_KEY | https://inference.generativeai.us-chicago-1.oci.oraclecloud.com/openai/v1 | @ai-sdk/openai-compatible | 9 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| cloudflare-ai-gateway | api_key | CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_GATEWAY_ID | SDK/default/options | ai-gateway-provider | 50 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| clarifai | api_key | CLARIFAI_PAT | https://api.clarifai.com/v2/ext/openai/v1 | @ai-sdk/openai-compatible | 12 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| aiand | api_key | AIAND_API_KEY | https://api.aiand.com/v1 | @ai-sdk/openai-compatible | 13 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| frogbot | api_key | FROGBOT_API_KEY | https://app.frogbot.ai/api/v1 | @ai-sdk/openai-compatible | 26 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| stackit | api_key | STACKIT_API_KEY | https://api.openai-compat.model-serving.eu01.onstackit.cloud/v1 | @ai-sdk/openai-compatible | 8 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| anyapi | api_key | ANYAPI_API_KEY | https://api.anyapi.ai/v1 | @ai-sdk/openai-compatible | 30 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| crusoe | api_key | CRUSOE_API_KEY | https://api.inference.crusoecloud.com/v1 | @ai-sdk/openai-compatible | 11 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| volcengine-coding-plan | api_key | ARK_CODING_PLAN_API_KEY | https://ark.cn-beijing.volces.com/api/coding/v3 | @ai-sdk/openai-compatible | 10 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| jiekou | api_key | JIEKOU_API_KEY | https://api.jiekou.ai/openai | @ai-sdk/openai-compatible | 61 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| ollama-cloud | api_key | OLLAMA_API_KEY | https://ollama.com/v1 | @ai-sdk/openai-compatible | 24 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| agentrouter | api_key | AGENTROUTER_API_KEY | https://agentrouter.org/v1 | @ai-sdk/openai-compatible | 5 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| tencent-tokenhub | api_key | TENCENT_TOKENHUB_API_KEY | https://tokenhub.tencentmaas.com/v1 | @ai-sdk/openai-compatible | 3 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| watsonx | api_key | WATSONX_AI_APIKEY, WATSONX_AI_PROJECT_ID | SDK/default/options | watsonx-ai-provider | 5 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| ambient | api_key | AMBIENT_API_KEY | https://api.ambient.xyz/v1 | @ai-sdk/openai-compatible | 12 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| model-oracle-ai | api_key | MODEL_ORACLE_API_KEY | https://api.modeloracle.com/api/v1 | @ai-sdk/openai-compatible | 15 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| xai | api_key | XAI_API_KEY | SDK/default/options | @ai-sdk/xai | 13 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| nebius | api_key | NEBIUS_API_KEY | https://api.tokenfactory.nebius.com/v1 | @ai-sdk/openai-compatible | 21 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| minimax-cn | api_key | MINIMAX_API_KEY | https://api.minimax.cn/anthropic/v1 | @ai-sdk/anthropic | 7 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| scaleway | api_key | SCALEWAY_API_KEY | https://api.scaleway.ai/v1 | @ai-sdk/openai-compatible | 16 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| vercel | api_key | AI_GATEWAY_API_KEY | SDK/default/options | @ai-sdk/gateway | 403 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| zai-coding-plan | api_key | ZHIPU_API_KEY | https://api.z.ai/api/coding/paas/v4 | @ai-sdk/openai-compatible | 7 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| ebcloud | api_key | EBCLOUD_API_KEY | https://maas-api.ebcloud.com/v1 | @ai-sdk/openai-compatible | 4 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| greenpt | api_key | GREENPT_API_KEY | https://api.greenpt.ai/v1 | @ai-sdk/openai-compatible | 40 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| mixlayer | api_key | MIXLAYER_API_KEY | https://models.mixlayer.ai/v1 | @ai-sdk/openai-compatible | 5 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| hyper | api_key | HYPER_API_KEY | https://hyper.charm.land/v1 | @ai-sdk/openai-compatible | 23 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| jalapeno | api_key | JALAPENO_API_KEY | https://api.jalapeno-cloud.ai/v1 | @ai-sdk/openai-compatible | 17 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| dinference | api_key | DINFERENCE_API_KEY | https://api.dinference.com/v1 | @ai-sdk/openai-compatible | 6 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| neosmith | api_key | NEOSMITH_API_KEY | https://router.neosmith.ai/v1 | @ai-sdk/openai | 4 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| fireworks-ai | api_key | FIREWORKS_API_KEY | https://api.fireworks.ai/inference/v1/ | @ai-sdk/openai-compatible | 22 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| stepfun-ai | api_key | STEPFUN_API_KEY | https://api.stepfun.ai/v1 | @ai-sdk/openai-compatible | 9 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| fastrouter | api_key | FASTROUTER_API_KEY | https://go.fastrouter.ai/api/v1 | @ai-sdk/openai-compatible | 47 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| orcarouter | api_key | ORCAROUTER_API_KEY | https://api.orcarouter.ai/v1 | @ai-sdk/openai-compatible | 117 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| friendli | api_key | FRIENDLI_TOKEN | https://api.friendli.ai/serverless/v1 | @ai-sdk/openai-compatible | 7 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| kimi-code-plan-cn | api_key | KIMI_API_KEY | https://api.kimi.com/coding/v1 | @ai-sdk/openai-compatible | 4 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| inco | api_key | INCO_API_KEY | https://api.inco.ai/v1 | @ai-sdk/openai-compatible | 7 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| sakana | api_key | SAKANA_API_KEY | https://api.sakana.ai/v1 | @ai-sdk/openai-compatible | 4 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| scx-ai | api_key | SCX_API_KEY | https://api.scx.ai/v1 | @ai-sdk/openai-compatible | 4 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| zenifra | api_key | ZENIFRA_AI_KEY | https://ai.zenifra.com/v1 | @ai-sdk/openai-compatible | 1 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| tokenrouter | api_key | TOKENROUTER_API_KEY | https://api.tokenrouter.com/v1 | @ai-sdk/openai-compatible | 1 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| google-vertex-anthropic | api_key | GOOGLE_VERTEX_PROJECT, GOOGLE_VERTEX_LOCATION, GOOGLE_APPLICATION_CREDENTIALS | SDK/default/options | @ai-sdk/google-vertex/anthropic | 16 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| moonshotai | api_key | MOONSHOT_API_KEY | https://api.moonshot.ai/v1 | @ai-sdk/openai-compatible | 4 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| ainetcafe | api_key | AINETCAFE_API_KEY | https://microquickjs.com/v1 | @ai-sdk/openai-compatible | 1 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| wallaby | api_key | WALLABY_API_KEY | https://api.wallabytoken.com/v1 | @ai-sdk/openai-compatible | 1 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| bee | api_key | BEE_API_KEY | https://api.bee.heossi.com/bee | @ai-sdk/openai-compatible | 6 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| scnet-token-plan | api_key | SCNET_API_KEY | https://api.scnet.cn/api/llm/v1 | @ai-sdk/openai-compatible | 19 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| ofox | api_key | OFOX_API_KEY | https://api.ofox.ai/v1 | @ai-sdk/openai-compatible | 151 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| neon | api_key | NEON_AI_GATEWAY_TOKEN, NEON_AI_GATEWAY_BASE_URL | ${NEON_AI_GATEWAY_BASE_URL}/v1 | @ai-sdk/openai-compatible | 47 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| aihubmix | api_key | AIHUBMIX_API_KEY | SDK/default/options | @aihubmix/ai-sdk-provider | 137 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| merge-gateway | api_key | MERGE_GATEWAY_API_KEY | https://api-gateway.merge.dev/v1/ai-sdk | merge-gateway-ai-sdk-provider | 195 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| opper | api_key | OPPER_API_KEY | https://api.opper.ai/v3/compat | @ai-sdk/openai-compatible | 57 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| nvidia | api_key | NVIDIA_API_KEY | https://integrate.api.nvidia.com/v1 | @ai-sdk/openai-compatible | 106 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| pioneer | api_key | PIONEER_API_KEY | https://api.fastino.ai/v1 | @ai-sdk/openai-compatible | 116 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| xiaomi | api_key | XIAOMI_API_KEY | https://api.xiaomimimo.com/v1 | @ai-sdk/openai-compatible | 9 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| xiaomi-token-plan-sgp | api_key | XIAOMI_API_KEY | https://token-plan-sgp.xiaomimimo.com/v1 | @ai-sdk/openai-compatible | 9 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| minimax | api_key | MINIMAX_API_KEY | https://api.minimax.io/anthropic/v1 | @ai-sdk/anthropic | 7 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| github-copilot | api_key | GITHUB_TOKEN | https://api.githubcopilot.com | @ai-sdk/openai-compatible | 34 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| inferx | api_key | INFERX_API_KEY | https://model.inferx.net/endpoints/v1 | @ai-sdk/openai-compatible | 12 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| opencode-go | api_key | OPENCODE_API_KEY | https://opencode.ai/zen/go/v1 | @ai-sdk/openai-compatible | 33 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| openreason | api_key | OPENREASON_API_KEY | https://api.openreason.app/v1 | @ai-sdk/openai-compatible | 3 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| aki-io | api_key | AKI_IO_API_KEY | https://aki.io/v1 | @ai-sdk/openai-compatible | 7 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| tensorx | api_key | TENSORX_API_KEY | https://api.tensorx.ai/v1 | @ai-sdk/openai-compatible | 25 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| longcat | api_key | LONGCAT_API_KEY | https://api.longcat.chat/openai | @ai-sdk/openai-compatible | 1 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| pareto | api_key | PARETO_API_KEY | https://api.paretoinference.com/v1 | @ai-sdk/openai-compatible | 1 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| chutes | api_key | CHUTES_API_KEY | https://llm.chutes.ai/v1 | @ai-sdk/openai-compatible | 14 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| edenai | api_key | EDENAI_API_KEY | https://api.edenai.run/v3 | @ai-sdk/openai-compatible | 291 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| stepfun | api_key | STEPFUN_API_KEY | https://api.stepfun.com/v1 | @ai-sdk/openai-compatible | 9 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| hpc-ai | api_key | HPC_AI_API_KEY | https://api.hpc-ai.com/inference/v1 | @ai-sdk/openai-compatible | 9 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| v0 | api_key | V0_API_KEY | SDK/default/options | @ai-sdk/vercel | 3 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| tencent-coding-plan | api_key | TENCENT_CODING_PLAN_API_KEY | https://api.lkeap.cloud.tencent.com/coding/v3 | @ai-sdk/openai-compatible | 8 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| tempr | api_key | TEMPR_API_KEY | https://api.temprhq.io/v1 | @ai-sdk/openai-compatible | 82 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| inception | api_key | INCEPTION_API_KEY | https://api.inceptionlabs.ai/v1/ | @ai-sdk/openai-compatible | 3 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| modelis | api_key | MODELIS_API_KEY | https://modelishub.com/v1 | @ai-sdk/openai-compatible | 9 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| opencode | api_key | OPENCODE_API_KEY | https://opencode.ai/zen/v1 | @ai-sdk/openai-compatible | 116 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| kenari | api_key | KENARI_API_KEY | https://kenari.id/v1 | @ai-sdk/openai-compatible | 60 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| kimi-code-plan-global | api_key | KIMI_API_KEY | https://api.kimi.ai/coding/v1 | @ai-sdk/openai-compatible | 4 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| trustedrouter | api_key | TRUSTEDROUTER_API_KEY | https://api.trustedrouter.com/v1 | @ai-sdk/openai-compatible | 7 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| wafer.ai | api_key | WAFER_API_KEY | https://pass.wafer.ai/v1 | @ai-sdk/openai-compatible | 5 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| zhipuai | api_key | ZHIPU_API_KEY | https://open.bigmodel.cn/api/paas/v4 | @ai-sdk/openai-compatible | 17 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| lynkr | api_key | LYNKR_API_KEY | http://127.0.0.1:8081/v1 | @ai-sdk/openai-compatible | 1 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| meganova | api_key | MEGANOVA_API_KEY | https://api.meganova.ai/v1 | @ai-sdk/openai-compatible | 19 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| ovhcloud | api_key | OVHCLOUD_API_KEY | https://oai.endpoints.kepler.ai.cloud.ovh.net/v1 | @ai-sdk/openai-compatible | 14 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| requesty | api_key | REQUESTY_API_KEY | https://router.requesty.ai/v1 | @ai-sdk/openai-compatible | 166 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| mistral | api_key | MISTRAL_API_KEY | SDK/default/options | @ai-sdk/mistral | 34 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| amazon-bedrock | api_key | AWS_BEARER_TOKEN_BEDROCK, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION | SDK/default/options | @ai-sdk/amazon-bedrock | 191 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| synthetic | api_key | SYNTHETIC_API_KEY | https://api.synthetic.new/openai/v1 | @ai-sdk/openai-compatible | 11 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| llmgateway | api_key | LLMGATEWAY_API_KEY | https://api.llmgateway.io/v1 | @ai-sdk/openai-compatible | 214 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| sap-ai-core | api_key | AICORE_SERVICE_KEY | SDK/default/options | @jerome-benoit/sap-ai-provider-v2 | 50 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| vivgrid | api_key | VIVGRID_API_KEY | https://api.vivgrid.com/v1 | @ai-sdk/openai | 36 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| klokintegration | api_key | KLOKINTEGRATION_API_KEY | https://api-gw.klok.ipaas.se/proxy/kloker-key/v1 | @ai-sdk/openai-compatible | 3 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| google-vertex | api_key | GOOGLE_VERTEX_PROJECT, GOOGLE_VERTEX_LOCATION, GOOGLE_APPLICATION_CREDENTIALS | SDK/default/options | @ai-sdk/google-vertex | 54 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| evroc | api_key | EVROC_API_KEY | https://models.think.evroc.com/v1 | @ai-sdk/openai-compatible | 16 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| tokengo | api_key | TOKENGO_API_KEY | https://api.tokengo.com/v1 | @ai-sdk/openai-compatible | 13 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| submodel | api_key | SUBMODEL_INSTAGEN_ACCESS_KEY | https://llm.submodel.ai/v1 | @ai-sdk/openai-compatible | 9 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| kosmik | api_key | KOSMIK_API_KEY | https://api.koscompute.com/v1 | @ai-sdk/openai-compatible | 1 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| tencent-token-plan | api_key | TENCENT_TOKEN_PLAN_API_KEY | https://api.lkeap.cloud.tencent.com/plan/v3 | @ai-sdk/openai-compatible | 2 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| togetherai | api_key | TOGETHER_API_KEY | SDK/default/options | @ai-sdk/togetherai | 29 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| helicone | api_key | HELICONE_API_KEY | https://ai-gateway.helicone.ai/v1 | @ai-sdk/openai-compatible | 90 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| cortecs | api_key | CORTECS_API_KEY | https://api.cortecs.ai/v1 | @ai-sdk/openai-compatible | 109 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| agnes | api_key | AGNES_API_KEY | https://apihub.agnes-ai.com/v1 | @ai-sdk/openai-compatible | 3 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| daoxe | api_key | DAOXE_API_KEY | https://daoxe.com/v1 | @ai-sdk/openai-compatible | 9 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| morph | api_key | MORPH_API_KEY | https://api.morphllm.com/v1 | @ai-sdk/openai-compatible | 3 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| alibaba-coding-plan-cn | api_key | ALIBABA_CODING_PLAN_API_KEY | https://coding.dashscope.aliyuncs.com/v1 | @ai-sdk/openai-compatible | 10 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| io-net | api_key | IOINTELLIGENCE_API_KEY | https://api.intelligence.io.solutions/api/v1 | @ai-sdk/openai-compatible | 17 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| infomaniak | api_key | INFOMANIAK_API_KEY, INFOMANIAK_PRODUCT_ID | https://api.infomaniak.com/2/ai/${INFOMANIAK_PRODUCT_ID}/openai/v1 | @ai-sdk/openai-compatible | 10 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| llmtech | api_key | LLMTECH_API_KEY | https://api.llmtech.eu/v1 | @ai-sdk/openai-compatible | 1 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| crossmodel | api_key | CROSSMODEL_API_KEY | https://api.crossmodel.ai/v1 | @ai-sdk/openai-compatible | 68 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| arcee | api_key | ARCEE_API_KEY | https://api.arcee.ai/api/v1 | @ai-sdk/openai-compatible | 7 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| drun | api_key | DRUN_API_KEY | https://chat.d.run/v1 | @ai-sdk/openai-compatible | 3 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| amd | api_key | AMD_API_KEY | https://developer.amd.com.cn/radeon/api/v1 | @ai-sdk/openai-compatible | 6 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| qvac | api_key | QVAC_API_KEY | SDK/default/options | @qvac/ai-sdk-provider | 9 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| claudinio | api_key | CLAUDINIO_API_KEY | https://api.claudin.io/v1 | @ai-sdk/openai-compatible | 2 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| runinfra | api_key | RUNINFRA_GATEWAY_KEY | https://api.runinfra.ai/v1 | @ai-sdk/openai-compatible | 7 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| hetzner | api_key | HETZNER_API_KEY | https://inference.hetzner.com/api/v1 | @ai-sdk/openai-compatible | 2 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| digitalocean | api_key | DIGITALOCEAN_ACCESS_TOKEN | https://inference.do-ai.run/v1 | @ai-sdk/openai-compatible | 101 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| aixy | api_key | AIXY_API_KEY | https://api.aixy-gateway.com/v1 | @ai-sdk/openai-compatible | 1 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |
| alibaba-cn | api_key | DASHSCOPE_API_KEY | https://dashscope.aliyuncs.com/compatible-mode/v1 | @ai-sdk/openai-compatible | 91 | Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED |

## Every declared backend API entry

All 124 extracted decorator entries are under backend. The route prefix below comes from its module APIRouter declaration, if present. Frontend /api/forgex is a proxy prefix, not part of the Python decorator. Decorator-declared routes are connected via create_app router inclusion; dynamic dependencies/services are described in the main report. The actual handler source link is provided rather than assuming frontend use.

| Method | Router-relative endpoint | Function | File / line |
|---|---|---|---|
| GET | `/agent-runtime/jobs` | `agent_jobs` | [backend/api/routes/agent_runtime.py:155](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:155) |
| GET | `/agent-runtime/tools` | `agent_tool_catalog` | [backend/api/routes/agent_runtime.py:163](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:163) |
| GET | `/agent-runtime/jobs/{job_id}` | `agent_job` | [backend/api/routes/agent_runtime.py:170](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:170) |
| POST | `/agent-runtime/jobs/{job_id}/pause` | `pause_agent_job` | [backend/api/routes/agent_runtime.py:176](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:176) |
| POST | `/agent-runtime/jobs/{job_id}/resume` | `resume_agent_job` | [backend/api/routes/agent_runtime.py:185](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:185) |
| POST | `/agent-runtime/jobs/{job_id}/budget` | `extend_agent_job` | [backend/api/routes/agent_runtime.py:194](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:194) |
| POST | `/agent-runtime/jobs/{job_id}/recovery-policy` | `agent_job_policy` | [backend/api/routes/agent_runtime.py:204](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:204) |
| GET | `/agent-runtime/projects/{project_id}/trust` | `project_agent_trust` | [backend/api/routes/agent_runtime.py:211](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:211) |
| POST | `/agent-runtime/projects/{project_id}/trust` | `trust_agent_project` | [backend/api/routes/agent_runtime.py:218](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:218) |
| GET | `/agent-runtime/tool-catalog` | `agent_tool_catalog` | [backend/api/routes/agent_runtime.py:227](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:227) |
| GET | `/agent-runtime/projects/{project_id}/memory` | `project_memory` | [backend/api/routes/agent_runtime.py:240](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:240) |
| POST | `/agent-runtime/projects/{project_id}/memory/invalidate` | `invalidate_project_memory` | [backend/api/routes/agent_runtime.py:253](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:253) |
| PATCH | `/agent-runtime/sessions/{session_id}` | `configure_session` | [backend/api/routes/agent_runtime.py:263](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:263) |
| DELETE | `/agent-runtime/sessions/{session_id}` | `delete_session` | [backend/api/routes/agent_runtime.py:277](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:277) |
| GET | `/agent-runtime/providers` | `agent_runtime_providers` | [backend/api/routes/agent_runtime.py:319](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:319) |
| GET | `/agent-runtime/sessions` | `list_agent_sessions` | [backend/api/routes/agent_runtime.py:328](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:328) |
| POST | `/agent-runtime/sessions` | `create_agent_session` | [backend/api/routes/agent_runtime.py:341](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:341) |
| GET | `/agent-runtime/sessions/{session_id}` | `get_agent_session` | [backend/api/routes/agent_runtime.py:373](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:373) |
| GET | `/agent-runtime/sessions/{session_id}/activity` | `agent_session_activity_events` | [backend/api/routes/agent_runtime.py:384](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:384) |
| POST | `/agent-runtime/sessions/{session_id}/messages` | `append_agent_session_message` | [backend/api/routes/agent_runtime.py:438](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:438) |
| POST | `/agent-runtime/runs/{run_id}/steer` | `steer_agent_runtime_run` | [backend/api/routes/agent_runtime.py:1039](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1039) |
| GET | `/agent-runtime/runs/{run_id}/queue` | `list_agent_runtime_queue` | [backend/api/routes/agent_runtime.py:1057](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1057) |
| PATCH | `/agent-runtime/runs/{run_id}/queue/{item_id}` | `edit_agent_runtime_queue` | [backend/api/routes/agent_runtime.py:1067](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1067) |
| DELETE | `/agent-runtime/runs/{run_id}/queue/{item_id}` | `remove_agent_runtime_queue` | [backend/api/routes/agent_runtime.py:1081](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1081) |
| POST | `/agent-runtime/sessions/{session_id}/cancel` | `cancel_agent_session` | [backend/api/routes/agent_runtime.py:1093](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1093) |
| POST | `/agent-runtime/runs` | `start_agent_runtime_run` | [backend/api/routes/agent_runtime.py:1139](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1139) |
| POST | `/agent-runtime/runs/{run_id}/confirm-flash` | `confirm_agent_runtime_flash` | [backend/api/routes/agent_runtime.py:1355](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1355) |
| GET | `/agent-runtime/runs/{run_id}` | `get_agent_runtime_run` | [backend/api/routes/agent_runtime.py:1382](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1382) |
| GET | `/agent-runtime/runs/{run_id}/build-evidence` | `agent_build_evidence` | [backend/api/routes/agent_runtime.py:1393](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1393) |
| POST | `/agent-runtime/runs/{run_id}/cancel` | `cancel_agent_runtime_run` | [backend/api/routes/agent_runtime.py:1425](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1425) |
| POST | `/agent-runtime/runs/{run_id}/retry` | `retry_agent_runtime_run` | [backend/api/routes/agent_runtime.py:1436](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1436) |
| POST | `/agent-runtime/runs/{run_id}/verify-draft` | `verify_agent_runtime_draft` | [backend/api/routes/agent_runtime.py:1467](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1467) |
| POST | `/agent-runtime/runs/{run_id}/discard-draft` | `discard_agent_runtime_draft` | [backend/api/routes/agent_runtime.py:1487](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1487) |
| POST | `/agent-runtime/runs/{run_id}/resume` | `resume_agent_runtime_run` | [backend/api/routes/agent_runtime.py:1499](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1499) |
| POST | `/agent-runtime/runs/{run_id}/apply-changes` | `apply_agent_runtime_changes` | [backend/api/routes/agent_runtime.py:1516](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1516) |
| GET | `/agent-runtime/runs/{run_id}/changes` | `get_agent_runtime_changes` | [backend/api/routes/agent_runtime.py:1545](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1545) |
| GET | `/agent-runtime/runs/{run_id}/events` | `agent_runtime_events` | [backend/api/routes/agent_runtime.py:1569](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1569) |
| GET | `/agent-runtime/runs/{run_id}/graph` | `agent_runtime_graph` | [backend/api/routes/agent_runtime.py:1640](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1640) |
| GET | `/agent-runtime/runs/{run_id}/context-artifacts/{artifact_id}` | `agent_runtime_context_artifact` | [backend/api/routes/agent_runtime.py:1652](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1652) |
| GET | `/agent-runtime/sessions/{session_id}/projection` | `agent_session_projection` | [backend/api/routes/agent_runtime.py:1683](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1683) |
| GET | `/agent-runtime/sessions/{session_id}/events` | `agent_session_events` | [backend/api/routes/agent_runtime.py:1693](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:1693) |
| POST | `/build/operations` | `accept_build` | [backend/api/routes/build.py:20](D:/projects/ForgeX-Phase6-Final/backend/api/routes/build.py:20) |
| GET | `/build/operations/{operation_id}` | `observe_build` | [backend/api/routes/build.py:40](D:/projects/ForgeX-Phase6-Final/backend/api/routes/build.py:40) |
| POST | `/build` | `build_project` | [backend/api/routes/build.py:60](D:/projects/ForgeX-Phase6-Final/backend/api/routes/build.py:60) |
| GET | `/changes` | `list_change_sets` | [backend/api/routes/changes.py:22](D:/projects/ForgeX-Phase6-Final/backend/api/routes/changes.py:22) |
| GET | `/changes/{change_set_id}` | `get_change_set` | [backend/api/routes/changes.py:27](D:/projects/ForgeX-Phase6-Final/backend/api/routes/changes.py:27) |
| POST | `/changes/{change_set_id}/apply` | `apply_change_set` | [backend/api/routes/changes.py:36](D:/projects/ForgeX-Phase6-Final/backend/api/routes/changes.py:36) |
| POST | `/changes/{change_set_id}/discard` | `discard_change_set` | [backend/api/routes/changes.py:46](D:/projects/ForgeX-Phase6-Final/backend/api/routes/changes.py:46) |
| POST | `/changes/{change_set_id}/undo` | `undo_change_set` | [backend/api/routes/changes.py:56](D:/projects/ForgeX-Phase6-Final/backend/api/routes/changes.py:56) |
| GET | `/v1/chat/sessions` | `list_sessions` | [backend/api/routes/chat.py:50](D:/projects/ForgeX-Phase6-Final/backend/api/routes/chat.py:50) |
| POST | `/v1/chat/sessions` | `create_session` | [backend/api/routes/chat.py:57](D:/projects/ForgeX-Phase6-Final/backend/api/routes/chat.py:57) |
| GET | `/v1/chat/sessions/{session_id}` | `get_session` | [backend/api/routes/chat.py:66](D:/projects/ForgeX-Phase6-Final/backend/api/routes/chat.py:66) |
| PATCH | `/v1/chat/sessions/{session_id}` | `configure_session` | [backend/api/routes/chat.py:71](D:/projects/ForgeX-Phase6-Final/backend/api/routes/chat.py:71) |
| DELETE | `/v1/chat/sessions/{session_id}` | `delete_session` | [backend/api/routes/chat.py:78](D:/projects/ForgeX-Phase6-Final/backend/api/routes/chat.py:78) |
| POST | `/v1/chat/sessions/{session_id}/cancel` | `cancel_session` | [backend/api/routes/chat.py:87](D:/projects/ForgeX-Phase6-Final/backend/api/routes/chat.py:87) |
| POST | `/v1/chat/sessions/{session_id}/messages` | `send_message` | [backend/api/routes/chat.py:95](D:/projects/ForgeX-Phase6-Final/backend/api/routes/chat.py:95) |
| GET | `/v1/chat/sessions/{session_id}/events` | `session_events` | [backend/api/routes/chat.py:131](D:/projects/ForgeX-Phase6-Final/backend/api/routes/chat.py:131) |
| GET | `/devices/boards` | `detected_boards` | [backend/api/routes/devices.py:23](D:/projects/ForgeX-Phase6-Final/backend/api/routes/devices.py:23) |
| POST | `/diagnostics/events` | `ingest_diagnostic_event` | [backend/api/routes/diagnostics.py:25](D:/projects/ForgeX-Phase6-Final/backend/api/routes/diagnostics.py:25) |
| GET | `/diagnostics/runs/{run_id}/export` | `export_run_diagnostics` | [backend/api/routes/diagnostics.py:38](D:/projects/ForgeX-Phase6-Final/backend/api/routes/diagnostics.py:38) |
| GET | `/diagnostics/runtime` | `diagnostic_runtime` | [backend/api/routes/diagnostics.py:65](D:/projects/ForgeX-Phase6-Final/backend/api/routes/diagnostics.py:65) |
| POST | `/execute` | `execute_workflow` | [backend/api/routes/execute.py:50](D:/projects/ForgeX-Phase6-Final/backend/api/routes/execute.py:50) |
| POST | `/execute/{task_id}/cancel` | `cancel_workflow` | [backend/api/routes/execute.py:240](D:/projects/ForgeX-Phase6-Final/backend/api/routes/execute.py:240) |
| GET | `/projects/{project_id}/files` | `list_project_files` | [backend/api/routes/files.py:41](D:/projects/ForgeX-Phase6-Final/backend/api/routes/files.py:41) |
| GET | `/projects/{project_id}/files/search` | `search_project_file_contents` | [backend/api/routes/files.py:64](D:/projects/ForgeX-Phase6-Final/backend/api/routes/files.py:64) |
| GET | `/files/content` | `get_file_content` | [backend/api/routes/files.py:102](D:/projects/ForgeX-Phase6-Final/backend/api/routes/files.py:102) |
| POST | `/files` | `create_file` | [backend/api/routes/files.py:141](D:/projects/ForgeX-Phase6-Final/backend/api/routes/files.py:141) |
| PUT | `/files` | `update_file` | [backend/api/routes/files.py:192](D:/projects/ForgeX-Phase6-Final/backend/api/routes/files.py:192) |
| DELETE | `/files` | `delete_file` | [backend/api/routes/files.py:286](D:/projects/ForgeX-Phase6-Final/backend/api/routes/files.py:286) |
| POST | `/flash` | `flash_project` | [backend/api/routes/flash.py:18](D:/projects/ForgeX-Phase6-Final/backend/api/routes/flash.py:18) |
| GET | `/harness/status` | `status` | [backend/api/routes/harness.py:17](D:/projects/ForgeX-Phase6-Final/backend/api/routes/harness.py:17) |
| GET | `/harness/providers` | `providers` | [backend/api/routes/harness.py:35](D:/projects/ForgeX-Phase6-Final/backend/api/routes/harness.py:35) |
| GET | `/health` | `health` | [backend/api/routes/health.py:17](D:/projects/ForgeX-Phase6-Final/backend/api/routes/health.py:17) |
| GET | `/v2/models/catalog` | `catalog` | [backend/api/routes/model_gateway.py:35](D:/projects/ForgeX-Phase6-Final/backend/api/routes/model_gateway.py:35) |
| PUT | `/v2/models/session-selection` | `select_session` | [backend/api/routes/model_gateway.py:101](D:/projects/ForgeX-Phase6-Final/backend/api/routes/model_gateway.py:101) |
| PUT | `/v2/models/role-bindings` | `select_role` | [backend/api/routes/model_gateway.py:106](D:/projects/ForgeX-Phase6-Final/backend/api/routes/model_gateway.py:106) |
| GET | `/v2/models/role-bindings` | `role_bindings` | [backend/api/routes/model_gateway.py:111](D:/projects/ForgeX-Phase6-Final/backend/api/routes/model_gateway.py:111) |
| GET | `/v2/models/session-selection` | `session_selection` | [backend/api/routes/model_gateway.py:122](D:/projects/ForgeX-Phase6-Final/backend/api/routes/model_gateway.py:122) |
| DELETE | `/v2/models/role-bindings/{role}` | `clear_binding` | [backend/api/routes/model_gateway.py:129](D:/projects/ForgeX-Phase6-Final/backend/api/routes/model_gateway.py:129) |
| GET | `/models/providers/{provider_id}/endpoint-health` | `endpoint_health` | [backend/api/routes/models.py:24](D:/projects/ForgeX-Phase6-Final/backend/api/routes/models.py:24) |
| GET | `/models/providers` | `list_providers` | [backend/api/routes/models.py:80](D:/projects/ForgeX-Phase6-Final/backend/api/routes/models.py:80) |
| POST | `/models/providers` | `create_custom_provider` | [backend/api/routes/models.py:86](D:/projects/ForgeX-Phase6-Final/backend/api/routes/models.py:86) |
| DELETE | `/models/providers/{provider_id}` | `delete_custom_provider` | [backend/api/routes/models.py:111](D:/projects/ForgeX-Phase6-Final/backend/api/routes/models.py:111) |
| POST | `/models/providers/{provider_id}/configure` | `configure_provider` | [backend/api/routes/models.py:131](D:/projects/ForgeX-Phase6-Final/backend/api/routes/models.py:131) |
| POST | `/models/providers/{provider_id}/health` | `provider_health` | [backend/api/routes/models.py:152](D:/projects/ForgeX-Phase6-Final/backend/api/routes/models.py:152) |
| GET | `/models/providers/{provider_id}/models` | `list_provider_models` | [backend/api/routes/models.py:170](D:/projects/ForgeX-Phase6-Final/backend/api/routes/models.py:170) |
| GET | `/models/selection` | `get_selection` | [backend/api/routes/models.py:197](D:/projects/ForgeX-Phase6-Final/backend/api/routes/models.py:197) |
| PUT | `/models/selection` | `save_selection` | [backend/api/routes/models.py:206](D:/projects/ForgeX-Phase6-Final/backend/api/routes/models.py:206) |
| GET | `/models/routes` | `list_routes` | [backend/api/routes/models.py:230](D:/projects/ForgeX-Phase6-Final/backend/api/routes/models.py:230) |
| POST | `/models/routes` | `save_route` | [backend/api/routes/models.py:239](D:/projects/ForgeX-Phase6-Final/backend/api/routes/models.py:239) |
| POST | `/models/test` | `test_model` | [backend/api/routes/models.py:261](D:/projects/ForgeX-Phase6-Final/backend/api/routes/models.py:261) |
| GET | `/models/usage` | `usage` | [backend/api/routes/models.py:295](D:/projects/ForgeX-Phase6-Final/backend/api/routes/models.py:295) |
| GET | `/models/generation-attempts` | `generation_attempts` | [backend/api/routes/models.py:302](D:/projects/ForgeX-Phase6-Final/backend/api/routes/models.py:302) |
| GET | `/models/chunked-generation-runs` | `chunked_generation_runs` | [backend/api/routes/models.py:310](D:/projects/ForgeX-Phase6-Final/backend/api/routes/models.py:310) |
| POST | `/models/generation-diagnostics/clear` | `clear_generation_diagnostics` | [backend/api/routes/models.py:318](D:/projects/ForgeX-Phase6-Final/backend/api/routes/models.py:318) |
| POST | `/monitor/start` | `start_monitor` | [backend/api/routes/monitor.py:27](D:/projects/ForgeX-Phase6-Final/backend/api/routes/monitor.py:27) |
| POST | `/monitor/stop` | `stop_monitor` | [backend/api/routes/monitor.py:71](D:/projects/ForgeX-Phase6-Final/backend/api/routes/monitor.py:71) |
| GET | `/monitor/status` | `monitor_status` | [backend/api/routes/monitor.py:95](D:/projects/ForgeX-Phase6-Final/backend/api/routes/monitor.py:95) |
| GET | `/projects/{project_id}/readiness` | `project_readiness` | [backend/api/routes/projects.py:24](D:/projects/ForgeX-Phase6-Final/backend/api/routes/projects.py:24) |
| GET | `/projects/{project_id}/profile` | `project_profile` | [backend/api/routes/projects.py:62](D:/projects/ForgeX-Phase6-Final/backend/api/routes/projects.py:62) |
| GET | `/projects` | `list_projects` | [backend/api/routes/projects.py:78](D:/projects/ForgeX-Phase6-Final/backend/api/routes/projects.py:78) |
| POST | `/projects/import` | `import_project` | [backend/api/routes/projects.py:103](D:/projects/ForgeX-Phase6-Final/backend/api/routes/projects.py:103) |
| POST | `/projects/{project_id}/refresh` | `refresh_project` | [backend/api/routes/projects.py:119](D:/projects/ForgeX-Phase6-Final/backend/api/routes/projects.py:119) |
| GET | `/projects/{project_id}` | `get_project` | [backend/api/routes/projects.py:137](D:/projects/ForgeX-Phase6-Final/backend/api/routes/projects.py:137) |
| DELETE | `/projects/{project_id}/recent` | `forget_recent_project` | [backend/api/routes/projects.py:151](D:/projects/ForgeX-Phase6-Final/backend/api/routes/projects.py:151) |
| POST | `/projects/{project_id}/locate` | `locate_recent_project` | [backend/api/routes/projects.py:160](D:/projects/ForgeX-Phase6-Final/backend/api/routes/projects.py:160) |
| DELETE | `/projects/{project_id}` | `delete_project` | [backend/api/routes/projects.py:175](D:/projects/ForgeX-Phase6-Final/backend/api/routes/projects.py:175) |
| GET | `/settings` | `get_settings` | [backend/api/routes/settings.py:28](D:/projects/ForgeX-Phase6-Final/backend/api/routes/settings.py:28) |
| GET | `/settings/schema` | `get_settings_schema` | [backend/api/routes/settings.py:33](D:/projects/ForgeX-Phase6-Final/backend/api/routes/settings.py:33) |
| PATCH | `/settings` | `patch_settings` | [backend/api/routes/settings.py:38](D:/projects/ForgeX-Phase6-Final/backend/api/routes/settings.py:38) |
| POST | `/settings/reset` | `reset_settings` | [backend/api/routes/settings.py:47](D:/projects/ForgeX-Phase6-Final/backend/api/routes/settings.py:47) |
| POST | `/settings/export` | `export_settings` | [backend/api/routes/settings.py:52](D:/projects/ForgeX-Phase6-Final/backend/api/routes/settings.py:52) |
| GET | `/terminal/profiles` | `terminal_profiles` | [backend/api/routes/terminal.py:47](D:/projects/ForgeX-Phase6-Final/backend/api/routes/terminal.py:47) |
| POST | `/terminal/sessions` | `start_terminal` | [backend/api/routes/terminal.py:67](D:/projects/ForgeX-Phase6-Final/backend/api/routes/terminal.py:67) |
| GET | `/terminal/sessions/{session_id}/output` | `terminal_output` | [backend/api/routes/terminal.py:88](D:/projects/ForgeX-Phase6-Final/backend/api/routes/terminal.py:88) |
| POST | `/terminal/sessions/{session_id}/input` | `terminal_input` | [backend/api/routes/terminal.py:106](D:/projects/ForgeX-Phase6-Final/backend/api/routes/terminal.py:106) |
| POST | `/terminal/sessions/{session_id}/clear` | `clear_terminal` | [backend/api/routes/terminal.py:121](D:/projects/ForgeX-Phase6-Final/backend/api/routes/terminal.py:121) |
| POST | `/terminal/sessions/{session_id}/resize` | `resize_terminal` | [backend/api/routes/terminal.py:134](D:/projects/ForgeX-Phase6-Final/backend/api/routes/terminal.py:134) |
| PATCH | `/terminal/sessions/{session_id}` | `rename_terminal` | [backend/api/routes/terminal.py:147](D:/projects/ForgeX-Phase6-Final/backend/api/routes/terminal.py:147) |
| DELETE | `/terminal/sessions/{session_id}` | `stop_terminal` | [backend/api/routes/terminal.py:166](D:/projects/ForgeX-Phase6-Final/backend/api/routes/terminal.py:166) |
| WEBSOCKET | `/terminal/sessions/{session_id}/stream` | `terminal_stream` | [backend/api/routes/terminal.py:176](D:/projects/ForgeX-Phase6-Final/backend/api/routes/terminal.py:176) |
| WEBSOCKET | `/ws/execution/{task_id}` | `execution_progress` | [backend/api/routes/websocket.py:19](D:/projects/ForgeX-Phase6-Final/backend/api/routes/websocket.py:19) |
| GET | `/build-history` | `build_history` | [backend/api/routes/workspace.py:29](D:/projects/ForgeX-Phase6-Final/backend/api/routes/workspace.py:29) |
| GET | `/logs` | `logs` | [backend/api/routes/workspace.py:54](D:/projects/ForgeX-Phase6-Final/backend/api/routes/workspace.py:54) |
