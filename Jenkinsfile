pipeline {
    agent {
        label 'jenkins_agent'
    }
    
    tools {
        dockerTool 'docker_default'
    }
    
    options {
        timestamps()
    }
    
    environment {
        WEBHOOK_URL = credentials("DISCORD_METATRON")
        NOTION_API_KEY = credentials("D2N_NOTION_API_KEY")

        PROJECT_NAME = "d2n"
        PROJECT_STATUS = "stable"
        LOG_LEVEL = "INFO"
        TZ = "Asia/Seoul"

        HOST_DIR = "/docker/d2n"
        D2N_DATABASE = "Jenkins"
        NETWORK = "net_outbound"
    }
    
    stages {
        stage('Test') {
            steps {
                sh '''
                    docker rm -f test-env || true
                    
                    docker create --name test-env -w /workspace python:3.14-trixie /bin/sh -c "
                        python -m venv .venv && \\
                        . .venv/bin/activate && \\
                        pip install --no-cache-dir -r requirements-dev.txt && \\
                        pytest && \\
                        mypy main.py src config
                    "
                    
                    docker cp . test-env:/workspace/
                    docker start -a test-env
                '''
            }
            post {
                always {
                    sh 'docker rm -f test-env || true'
                }
            }
        }
        
        stage('Docker build') {
            steps {
                sh '''
                    docker build -t ${PROJECT_NAME}:${PROJECT_STATUS}-${BUILD_NUMBER} .
                    docker tag ${PROJECT_NAME}:${PROJECT_STATUS}-${BUILD_NUMBER} ${PROJECT_NAME}:latest
                '''
            }
        }
        
        stage('Remove old docker container') {
            steps {
                sh '''
                    docker stop ${PROJECT_NAME} || true
                    docker rm ${PROJECT_NAME} || true
                '''
            }
        }
        
        stage('Run new docker container') {
            steps {
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
                        --label "d2n.database=${D2N_DATABASE}" \
                        --label "com.centurylinklabs.watchtower.enable=false" \
                        --restart unless-stopped \
                        --network ${NETWORK} \
                        ${PROJECT_NAME}:latest
                '''
            }
        }
        
        stage('Cleanup dangling images') {
            steps {
                sh 'docker image prune -f || true'
            }
        }
    }
    
    post {
        success {
            discordSend description: "Build Success",
                        footer: "Application Deployed Successfully",
                        link: env.BUILD_URL, 
                        result: currentBuild.currentResult,
                        title: "${env.JOB_NAME} #${BUILD_NUMBER}",
                        webhookURL: env.WEBHOOK_URL
        }
        failure {
            discordSend description: "Build Fail",
                        footer: "Application Deployed Failed",
                        link: env.BUILD_URL, 
                        result: currentBuild.currentResult,
                        title: "${env.JOB_NAME} #${BUILD_NUMBER}",
                        webhookURL: env.WEBHOOK_URL
        }
    }
}
