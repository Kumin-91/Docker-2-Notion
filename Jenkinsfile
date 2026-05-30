pipeline
{
    agent
    {
        label 'jenkins_agent'
    }
    tools
    {
        dockerTool 'docker_default'
    }
    options
    {
        timestamps()
    }
    environment
    {
        // Discord 알림 (기존 공용 크리덴셜 재사용)
        WEBHOOK_URL = credentials("DISCORD_METATRON")
        // Notion API 키: 이미지에 남기지 않고 실행 시점에만 주입 (Jenkins Secret text 크리덴셜)
        NOTION_API_KEY = credentials("D2N_NOTION_API_KEY")

        PROJECT_NAME = "d2n"
        PROJECT_STATUS = "beta"

        // 호스트에 미리 준비: ${HOST_DIR}/config/config.yaml (logs/data 는 자동 생성)
        HOST_DIR = "/docker/d2n"

        LOG_LEVEL = "INFO"
        TZ = "Asia/Seoul"
    }
    stages
    {
        stage('Test')
        {
            steps
            {
                script
                {
                    echo "Running tests - pytest & mypy (docker build --target test)"
                    // 플러그인의 docker.image().inside 는 오래된 클라이언트(API 1.29)를 써서
                    // 최신 데몬(>=1.40)에 거부당하므로, 정상 동작하는 docker build 로 테스트한다.
                    sh 'docker build --target test -t ${PROJECT_NAME}:test .'
                }
            }
        }
        stage('Docker build')
        {
            steps
            {
                script
                {
                    echo "Building docker image"
                    sh '''
                        docker build -t ${PROJECT_NAME}:${PROJECT_STATUS}-${BUILD_NUMBER} .
                        docker tag ${PROJECT_NAME}:${PROJECT_STATUS}-${BUILD_NUMBER} ${PROJECT_NAME}:latest
                    '''
                }
            }
        }
        stage('Remove old docker container')
        {
            steps
            {
                script
                {
                    echo "Removing old docker container"
                    sh '''
                        docker stop ${PROJECT_NAME} || true
                        docker rm ${PROJECT_NAME} || true
                    '''
                }
            }
        }
        stage('Run new docker container')
        {
            steps
            {
                script
                {
                    echo "Running new docker container"
                    // D2N은 Docker 이벤트를 감시하므로 docker.sock 을 마운트합니다.
                    sh '''
                        docker run -d \
                            --name ${PROJECT_NAME} \
                            -v /var/run/docker.sock:/var/run/docker.sock \
                            -v ${HOST_DIR}/config:/app/config \
                            -v ${HOST_DIR}/logs:/app/logs \
                            -v ${HOST_DIR}/data:/app/data \
                            -e DOCKER_API_URL="unix:///var/run/docker.sock" \
                            -e NOTION_API_KEY="${NOTION_API_KEY}" \
                            -e LOG_LEVEL="${LOG_LEVEL}" \
                            -e TZ="${TZ}" \
                            --label "d2n.enabled=true" \
                            --label "d2n.database=Jenkins" \
                            --label "com.centurylinklabs.watchtower.enable=false" \
                            --restart unless-stopped \
                            ${PROJECT_NAME}:latest
                    '''
                }
            }
        }
        stage('Cleanup dangling images')
        {
            steps
            {
                script
                {
                    echo "Pruning dangling images"
                    sh 'docker image prune -f || true'
                }
            }
        }
    }
    post
    {
        success
        {
            discordSend description: "Build Success",
                            footer: "Application Deployed Successfully",
                            link: env.BUILD_URL, result: currentBuild.currentResult,
                            title: "${env.JOB_NAME} #${BUILD_NUMBER}",
                            webhookURL: env.WEBHOOK_URL
        }
        failure
        {
            discordSend description: "Build Fail",
                            footer: "Application Deployed Failed",
                            link: env.BUILD_URL, result: currentBuild.currentResult,
                            title: "${env.JOB_NAME} #${BUILD_NUMBER}",
                            webhookURL: env.WEBHOOK_URL
        }
    }
}
